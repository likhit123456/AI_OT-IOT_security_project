"""Live control room using the demo GGUF and one managed local runner."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import psutil

from monitor.observer_runtime import Request, make_observer
from training.local_gguf_server import URL, ensure, register
from training.shared_slm_data import ROOT, sha
from training.train_demo_observer import assessment

ALIAS = "scram-observer-demo-f16"
DESTINATION = ROOT / "model/scram-shared-observer"


def lease_owner(run):
    path = run / "control-room.lock"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
        process = psutil.Process(value["pid"])
        if (abs(process.create_time()-value["create_time"]) < .01
                and process.cmdline() == value["command"]):
            return value
    except psutil.NoSuchProcess:
        return None
    except (ValueError, TypeError, KeyError, psutil.AccessDenied) as exc:
        raise RuntimeError("Another launch may be starting; cannot verify its lease safely") from exc
    return None


@contextmanager
def control_room_lease(run=DESTINATION, port=11436):
    # The OS releases this mutex even after a crash. It also serialises stale
    # file recovery, so two simultaneous launches cannot archive each other's lock.
    with socket.socket() as mutex:
        if os.name == "nt":
            mutex.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            mutex.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError("A demo launch is already running or its launch port is occupied") from exc
        run.mkdir(parents=True, exist_ok=True)
        path = run / "control-room.lock"
        if path.exists():
            if lease_owner(run) is not None:
                raise RuntimeError("A demo launch is already running; leaving its app/model alone")
            path.rename(run / f"control-room.stale-{uuid.uuid4().hex}.json")
        process = psutil.Process(os.getpid())
        record = {"pid": process.pid, "create_time": process.create_time(),
                  "command": process.cmdline(), "token": uuid.uuid4().hex,
                  "mutex_port": mutex.getsockname()[1]}
        with path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream)
        try:
            yield record
        finally:
            if path.exists() and json.loads(path.read_text()).get("token") == record["token"]:
                path.unlink()


def deployment(run=DESTINATION):
    folder = run / "app-deployment"
    manifest = json.loads((folder / "deployment.json").read_text(encoding="utf-8"))
    if (manifest.get("alias") != ALIAS or manifest.get("schema") != "measured-evidence-v2"
            or manifest.get("demo_only") is not True
            or Path(manifest["weights_file"]).name != manifest["weights_file"]):
        raise ValueError("Unexpected demo model identity or schema")
    weights = folder / manifest["weights_file"]
    if sha(weights) != manifest["weights_sha256"]:
        raise ValueError("Demo model hash changed")
    report = json.loads((run / "f16-development-validation.json").read_text())
    if (sha(run / "f16-development-validation.json") != manifest["validation_report_sha256"]
            or report["weights_sha256"] != manifest["weights_sha256"]
            or not assessment(report["rows"])["demo_target_met"]):
        raise ValueError("Export does not meet its fixed demo validation target")
    return weights, manifest


def settings():
    return {"SCRAM_OBSERVER_MODE": "unified", "SCRAM_OBSERVER_SCHEMA": "measured-evidence-v2",
            "SCRAM_OBSERVER_MODEL": ALIAS, "SCRAM_OBSERVER_BACKEND": "llama_cpp",
            "SCRAM_OBSERVER_URL": URL, "SCRAM_OBSERVER_CONTEXT": "3072",
            "SCRAM_OBSERVER_EVIDENCE_REVIEW": "1"}


def checked_process(record):
    try:
        process = psutil.Process(record["pid"])
        if process.cmdline() != record["command"]:
            raise RuntimeError("Recorded PID belongs to another process; leaving it alone")
        if "create_time" in record and abs(process.create_time()-record["create_time"]) > .01:
            raise RuntimeError("Recorded PID was reused; leaving it alone")
        return process
    except psutil.NoSuchProcess:
        return None


def start(run=DESTINATION):
    owner = lease_owner(run)
    if owner is not None and owner["pid"] != os.getpid():
        raise RuntimeError("Another demo launcher owns the model service")
    weights, _ = deployment(run)
    previous_path = run / "llama-server/process.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    if not previous or checked_process(previous) is None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 11437))
    register(run, ALIAS, weights, 3072)
    record = ensure(run, ALIAS)
    record["create_time"] = psutil.Process(record["pid"]).create_time()
    (run / "app-service-process.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def stop(run=DESTINATION):
    path = run / "app-service-process.json"
    if path.exists():
        process = checked_process(json.loads(path.read_text()))
        if process:
            process.terminate()
            process.wait(20)


def check(run=DESTINATION, output=None):
    path = output or run / "application-integration.json"
    if path.exists():
        raise FileExistsError(path)
    before = {k: os.environ.get(k) for k in settings()}
    os.environ.update(settings())
    runtime = None
    try:
        runtime = make_observer()
        observer = runtime.llm
        inputs = json.loads((run / "demo-inputs.json").read_text())
        outputs = []
        for index, value in enumerate(inputs, 1):
            kind = value["input"]
            now = observer.clock()
            observer.paths[kind].sequence = index
            request = Request(kind, json.dumps(value), observer.generation, index, 0,
                              now, now+observer.deadline_s, observer.run_id)
            observer.worker.submit(request)
            if not runtime.wait_idle(35):
                raise TimeoutError("Application observer worker did not become idle")
            result = observer.last.get(kind)
            if result is None or result.label is None:
                raise RuntimeError(f"Application observer did not return a valid {kind} verdict")
            outputs.append(result.as_dict())
        snapshot = observer.snapshot()
        observer.reset()
        if observer.last or observer.worker.pending:
            raise RuntimeError("Reset did not clear observer state")
        report = {"status": "verified", "settings": settings(), "verdicts": outputs,
                  "snapshot": snapshot, "reset_clears_history_and_pending": True,
                  "generation_after_reset": observer.generation, "plant_writes": False,
                  "independent_final_test": False, "release_qualified": False}
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
        return report
    finally:
        if runtime:
            runtime.close()
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify both roles through the real app worker; no plant writes")
    parser.add_argument("--check-output", type=Path, help="Save a fresh health-check report without overwriting an earlier one")
    args, remaining = parser.parse_known_args()
    if args.check_output and not args.check:
        parser.error("--check-output requires --check")
    with control_room_lease():
        # Avoid starting another application beside an existing plant writer.
        for port in (8765, 8770):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        record = start()
        print(f"Demo observer: {ALIAS}, local llama.cpp PID {record['pid']}", flush=True)
        try:
            if args.check:
                result = check(output=args.check_output)
                print(json.dumps({"status": result["status"], "verdicts": len(result["verdicts"])}))
                return 0
            print("Control room uses the baseline operator and trained shared observer. Ctrl+C stops both.", flush=True)
            return subprocess.call([sys.executable, "-m", "live", "--brain", "baseline", "--no-reset",
                                    "--no-ollama-fix", *remaining], cwd=ROOT, env={**os.environ, **settings()})
        finally:
            stop()


if __name__ == "__main__":
    raise SystemExit(main())
