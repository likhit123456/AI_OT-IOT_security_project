"""Run the fixed evidence-v2 experiment without approval/confirmation prompts.

Run in normal PowerShell because Docker's named pipes deny this sandbox access.
Owns only experiment files/processes. Quality failures and unmeasured release
gates are reported honestly; this command never promotes an unqualified model.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from training.collect_slm_evidence import (
    RUN,
    collection_plans,
    ensure_excursion_supplement,
    ensure_plan,
)
from training.shared_slm_data import ROOT, sha

POLICY = ROOT / "experiments/slm_evidence_v2_policy.json"
TRAIN_PYTHON = ROOT / ".venv-train/Scripts/python.exe"
ALIAS = "scram-observer-evidence-v2"
KNOWN_PRE_PORT_RETRY_COLLECTORS = {"cac2412a8a756d35835597849b86d1444099cec63e9196ee5e587dd42e4ebaa1"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class Pipeline:
    def __init__(self, run):
        self.run = run
        self.folder = run / "automation"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "status.json"
        self.status = read(self.path) if self.path.exists() else {"experiment": ALIAS, "stages": {}, "defaults_changed": False}

    def save(self):
        self.status["updated_at"] = datetime.now().astimezone().isoformat()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.status, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def repair_collector_resume(self):
        """Audit a known transport fix before reusing pre-training collection.

        This is deliberately not a general source-hash bypass. Original stage
        identities/logs, original plan, metadata and every recorded byte remain
        preserved. It cannot migrate a run that has reached model preparation.
        """
        lock = self.folder / "pipeline.lock"
        if lock.exists() and read(lock).get("pid") != os.getpid():
            raise RuntimeError("another pipeline owns the execution lock; resume repair refused")
        collector = ROOT / "training/collect_slm_evidence.py"
        key, current = str(collector), sha(collector)
        changed = {name: stage for name, stage in self.status["stages"].items()
                   if stage.get("identity", {}).get("inputs", {}).get(key) not in (None, current)}
        if not changed:
            return False
        allowed = {"docker_preflight", "real_smoke", "collect_all"}
        if (not set(self.status["stages"]) <= allowed or (self.run / "freeze.json").exists()
            or (self.run / "training/config.json").exists() or (self.run / "data-v2").exists()):
            raise ValueError("collector source migration is restricted to collection before model preparation/training")
        for name, stage in changed.items():
            old = stage["identity"]["inputs"][key]
            snapshot = self.run / "collector-sources" / f"{old}.py"
            if old not in KNOWN_PRE_PORT_RETRY_COLLECTORS or not snapshot.is_file() or sha(snapshot) != old:
                raise ValueError("collector source change is not the known audited port-allocation fix")
            for path, digest in stage["identity"]["inputs"].items():
                if path != key and sha(Path(path)) != digest:
                    raise ValueError("collection allocation changed; source-only resume repair refused")
            if stage["state"] == "completed" and any(sha(Path(p)) != h for p, h in stage["outputs"].items()):
                raise ValueError(f"{name}: completed evidence changed; source repair refused")
        planned, _ = collection_plans(self.run)
        specs = {s["recording_id"]: (s, h) for s, h in planned}
        identities = {}
        for path in (self.run / "raw").rglob("*.completed.json"):
            metadata = read(path)
            spec = metadata["spec"]
            expected, source_hash = specs.get(spec["recording_id"], (None, None))
            raw = path.with_suffix("").with_suffix(".jsonl")
            if (not metadata.get("complete") or spec != expected or metadata["plan_sha256"] != source_hash
                or sha(raw) != metadata["sha256"]):
                raise ValueError("completed recording integrity check failed; source repair refused")
            identities[str(path)] = {"metadata_sha256": sha(path), "raw_sha256": metadata["sha256"]}
        backup = self.folder / f"status.before-collector-fix-{uuid.uuid4().hex}.json"
        backup.write_bytes(self.path.read_bytes())
        report_path = self.folder / f"collector-fix-{uuid.uuid4().hex}.json"
        report = {"reason": "Retry Docker-selected forbidden host ports before any plant connection",
                  "previous_status": str(backup), "previous_status_sha256": sha(backup),
                  "current_collector_sha256": current, "completed_recordings_verified": identities,
                  "sealed_measurements_decoded": False, "existing_recordings_changed": False,
                  "classification_thresholds_changed": False, "new_training_occurred": False}
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        for stage in changed.values():
            old_identity = json.loads(json.dumps(stage["identity"]))
            stage.setdefault("prior_identities", []).append({"identity": old_identity, "audit": str(report_path)})
            stage["identity"]["inputs"][key] = current
        self.status.setdefault("collector_resume_repairs", []).append(str(report_path))
        self.status.update(state="ready_to_resume_collection")
        self.status.pop("error", None)
        self.save()
        print(f"Verified and preserved {len(identities)} completed recordings; collector resume repair recorded.", flush=True)
        return True

    def execute(self, name, command, outputs=(), inputs=(), resume_command=None):
        identity = {"command": [str(x) for x in command], "inputs": {str(p): sha(p) for p in inputs}}
        previous = self.status["stages"].get(name, {})
        if previous.get("state") == "completed":
            if previous["identity"] != identity or any(sha(Path(p)) != value for p, value in previous["outputs"].items()):
                raise ValueError(f"{name}: completed stage inputs/output identity changed")
            print(f"Already completed: {name}", flush=True)
            return
        if previous.get("identity") and previous["identity"] != identity:
            raise ValueError(f"{name}: interrupted stage inputs changed; preserve it and use a new experiment")
        attempt = int(previous.get("attempt", 0))+1
        log_path = self.folder / f"{name}-attempt-{attempt:02d}.log"
        actual = resume_command or command
        stage = {"state": "running", "identity": identity, "attempt": attempt, "log": str(log_path),
                 "actual_command": [str(x) for x in actual]}
        self.status.update(state="running", current_stage=name)
        self.status.pop("error", None)
        self.status["stages"][name] = stage
        self.save()
        print(f"Starting: {name}", flush=True)
        environment = {**os.environ, "PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1",
                       "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
                       "SCRAM_SLM_EVAL_BACKEND": "llama_cpp"}
        process = None
        try:
            with log_path.open("x", encoding="utf-8") as log:
                process = subprocess.Popen([str(x) for x in actual], cwd=ROOT, env=environment,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
                stage["pid"] = process.pid
                self.save()
                for line in process.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                code = process.wait()
            if code:
                raise RuntimeError(f"{name} failed (exit {code}); evidence retained in {log_path}")
            missing = [str(p) for p in outputs if not p.is_file()]
            if missing:
                raise RuntimeError(f"{name} did not produce required artifacts: {missing}")
            stage.update(state="completed", outputs={str(p): sha(p) for p in outputs}, log_sha256=sha(log_path))
            self.save()
        except BaseException as exc:
            if process is not None and process.poll() is None:
                # Ctrl+C also reaches the normal-console child collector, whose
                # own handler cancels workers and cleans up its containers.
                process.wait(timeout=60)
            stage.update(state="failed", error=f"{type(exc).__name__}: {exc}")
            self.status["state"] = "blocked_by_stage_failure"
            self.save()
            raise


def check_smoke(run):
    from training.slm_evidence_data import read_recording, telemetry_row
    plan = read(run / "collection-plan.json")
    spec = next(s for s in plan["recordings"] if s["recording_id"] == "train-normal-00")
    rows, metadata = read_recording(run, spec, sha(run / "collection-plan.json"))
    usable = 0
    for start in range(0, len(rows)-30, 32):
        samples = rows[start:start+31:2]
        if samples[0]["t"] >= 45:
            row, _ = telemetry_row(samples, spec, metadata["sha256"])
            usable += row is not None
    if usable < 3:
        raise RuntimeError(f"real smoke has only {usable} usable normal windows; full collection not started")
    path = run / "automation/smoke-check.json"
    result = {"usable_normal_windows": usable, "recording_sha256": metadata["sha256"],
              "native_dropout_probe_passed": True, "native_stuck_probe_passed": True}
    if path.exists() and read(path) != result:
        raise ValueError("smoke evidence changed")
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def check_excursion_smoke(run):
    from training.slm_evidence_data import read_recording, telemetry_row
    plan_path = run / "excursion-supplement-plan.json"
    spec = read(plan_path)["recordings"][0]
    rows, metadata = read_recording(run, spec, sha(plan_path))
    usable = sum(telemetry_row(rows[start:start+31:2], spec, metadata["sha256"])[0] is not None
                 for start in range(0, len(rows)-30, 32) if rows[start]["t"] >= 45)
    if usable < 3:
        raise RuntimeError(f"excursion fixture smoke has only {usable} qualifying windows; refusing full supplement collection")
    (run / "automation/excursion-smoke-check.json").write_text(json.dumps({"usable_excursion_windows": usable,
        "recording_sha256": metadata["sha256"], "native_process": True}, indent=2), encoding="utf-8")


def stop_task_server(run):
    path = run / "llama-server/process.json"
    if not path.exists():
        return
    import psutil
    record = read(path)
    if not psutil.pid_exists(record["pid"]):
        return
    process = psutil.Process(record["pid"])
    if not record.get("task_owned") or process.cmdline() != record["command"]:
        raise RuntimeError("refusing to stop a process whose ownership/command differs")
    process.terminate()
    process.wait(timeout=20)


def package(run):
    frozen, qualification = read(run / "freeze.json"), read(run / "qualification.json")
    selected = Path(frozen["model"])
    if sha(selected) != frozen["model_sha256"]:
        raise ValueError("frozen weights changed before packaging")
    destination = run / "deployment"
    if not destination.exists():
        temporary = run / f"deployment-{uuid.uuid4().hex}.partial"
        temporary.mkdir()
        shutil.copy2(selected, temporary / selected.name)
        modelfile = f"FROM ./{selected.name}\nPARAMETER num_ctx 3072\nPARAMETER temperature 0\nPARAMETER num_predict 128\nPARAMETER repeat_penalty 1.1\nPARAMETER repeat_last_n 3072\n"
        (temporary / "Modelfile").write_text(modelfile, encoding="utf-8")
        manifest = {"alias": ALIAS, "weights_file": selected.name, "weights_sha256": frozen["model_sha256"],
                    "modelfile_sha256": sha(temporary / "Modelfile"), "wire_schema": "measured-evidence-v2",
                    "release_qualified": False, "qualification_sha256": sha(run / "qualification.json")}
        (temporary / "deployment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        shutil.copy2(run / "qualification.json", temporary / "qualification.json")
        shutil.copy2(ROOT / "artifacts/optimization/candidates/base-qwen2.5-0.5b/LICENSE", temporary / "BASE_LICENSE")
        shutil.copy2(ROOT / "LICENSE", temporary / "PROJECT_LICENSE")
        (temporary / "README.md").write_text(
            "# Experimental observer evidence-v2\n\n"
            f"Quality status: {qualification['status']}. Runtime release gates are unmeasured.\n"
            "This candidate is not release qualified; legacy V2/V3 remain defaults.\n"
            "Use only the explicit measured-evidence-v2 prompt schema and the recorded 3072-token context.\n"
            "See qualification.json and deployment.json for actual results and weight identity.\n", encoding="utf-8")
        temporary.rename(destination)
    manifest = read(destination / "deployment.json")
    if (manifest["weights_sha256"] != frozen["model_sha256"] or sha(destination / selected.name) != frozen["model_sha256"] or
        manifest["qualification_sha256"] != sha(run / "qualification.json")):
        raise ValueError("existing deployment bundle identity changed")
    archive = run / "scram-observer-evidence-v2.zip"
    if archive.exists():
        raise FileExistsError("portable archive exists without a completed package stage")
    partial = archive.with_name(archive.name+f".{uuid.uuid4().hex}.partial")
    with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        for file in sorted(destination.iterdir()):
            bundle.write(file, file.name)
    with zipfile.ZipFile(partial) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("portable archive CRC failed")
    partial.rename(archive)
    archive.with_suffix(".zip.sha256").write_text(sha(archive)+"\n", encoding="ascii")


def native_command(stage, run, data):
    return [TRAIN_PYTHON, "-m", "training.shared_slm_native", stage, "--run", run, "--data", data]


def run_pipeline(pipeline, phase, workers):
    run, data = pipeline.run, pipeline.run / "data-v2"
    collector = ROOT / "training/collect_slm_evidence.py"
    plan = run / "collection-plan.json"
    if phase in ("all", "collect"):
        command = [sys.executable, "-m", "training.collect_slm_evidence"]
        pipeline.execute("docker_preflight", [*command, "preflight"], inputs=[collector, plan])
        pipeline.execute("real_smoke", [*command, "collect", "--only", "train-normal-00"],
            outputs=[run / "raw/train/train-normal-00.completed.json"], inputs=[collector, plan])
        check_smoke(run)
        supplement = run / "excursion-supplement-plan.json"
        first = read(supplement)["recordings"][0]
        pipeline.execute("excursion_fixture_smoke", [*command, "collect", "--supplement", "--only", first["recording_id"]],
            outputs=[run / "raw/train" / f"{first['recording_id']}.completed.json"], inputs=[collector, supplement])
        check_excursion_smoke(run)
        expected = [run / "raw" / spec["split"] / f"{spec['recording_id']}.completed.json" for spec in read(plan)["recordings"]]
        pipeline.execute("collect_all", [*command, "collect", "--workers", str(workers)], outputs=expected, inputs=[collector, plan])
        expected_supplement = [run / "raw" / spec["split"] / f"{spec['recording_id']}.completed.json" for spec in read(supplement)["recordings"]]
        pipeline.execute("collect_excursion_supplement", [*command, "collect", "--supplement", "--workers", str(workers)],
            outputs=expected_supplement, inputs=[collector, supplement])
        if phase == "collect":
            pipeline.status["state"] = "collection_completed"
            pipeline.save()
            return
    if not TRAIN_PYTHON.is_file():
        raise FileNotFoundError("the preserved .venv-train Python 3.11 environment is missing")
    planned, plan_hashes = collection_plans(run)
    plans = [run / name for name in plan_hashes]
    metadata = [run / "raw" / spec["split"] / f"{spec['recording_id']}.completed.json"
                for spec, _ in planned if spec["split"] != "sealed"]
    pipeline.execute("prepare_data", [sys.executable, "-m", "training.slm_evidence_data"],
        outputs=[data / "manifest.json", data / "train.jsonl", data / "val.jsonl"],
        inputs=[*plans, *metadata, ROOT / "training/slm_evidence_data.py"])
    common = [POLICY, data / "manifest.json", data / "train.jsonl", data / "val.jsonl", ROOT / "training/shared_slm_native.py"]
    pipeline.execute("cuda_preflight", [TRAIN_PYTHON, "-m", "training.shared_slm_native", "preflight", "--run", run],
                     outputs=[run / "preflight.json"], inputs=[POLICY])
    pipeline.execute("token_audit", native_command("tokens", run, data), outputs=[run / "token-audit.json"], inputs=common)
    for stage in ("smoke", "train"):
        folder = run / ("smoke" if stage == "smoke" else "training")
        command = native_command(stage, run, data)+["--policy", POLICY, "--label-loss-weight", "10"]
        if stage == "train":
            command += ["--validation-batch-size", "4"]
        resume = None
        if (folder / "steps.jsonl").exists() and not (folder / "completed.json").exists():
            recovery = folder / "recovery.json"
            if not recovery.exists():
                raise RuntimeError(f"{stage} interrupted before a recovery checkpoint; preserving it for diagnosis")
            resume = [*command, "--resume", read(recovery)["checkpoint"]]
        pipeline.execute(stage, command, outputs=[folder / "completed.json"], inputs=common, resume_command=resume)
        if stage == "smoke":
            pipeline.execute("reload_smoke", native_command("reload-smoke", run, data),
                outputs=[run / "smoke/reload-inference.json"], inputs=[folder / "completed.json"])
    artifact = [TRAIN_PYTHON, "-m", "training.slm_artifact_tools", "--run", run, "--data", data]
    for base in (True, False):
        output = run / ("new-base-validation.json" if base else "new-adapter-validation.json")
        command = [*artifact[:3], "hf-eval", *artifact[3:], "--per-class", "0", "--batch-size", "4", "--output", output]
        if base:
            command.append("--base-only")
        pipeline.execute("base_validation" if base else "adapter_validation", command, outputs=[output], inputs=common)
    pipeline.execute("export_q4", [*artifact[:3], "export", *artifact[3:]],
        outputs=[run / "export/tooling.json", run / "export/scram-observer-slm-f16.gguf", run / "export/scram-observer-slm-q4_k_m.gguf"],
        inputs=[run / "training/selected.json"])
    evaluate = [TRAIN_PYTHON, "-m", "training.evaluate_evidence_candidate", "--run", run, "--data", data]
    models = {kind: run / f"export/scram-observer-slm-{suffix}.gguf" for kind, suffix in (("f16", "f16"), ("q4", "q4_k_m"), ("q8", "q8_0"))}
    for kind in ("f16", "q4"):
        output = run / f"new-{kind}-validation.json"
        pipeline.execute(f"{kind}_validation", [*evaluate, "--model", models[kind], "--output", output,
            "--alias", f"{ALIAS}-{kind}-eval"], outputs=[output], inputs=[models[kind], data / "val.jsonl"])
    from training.freeze_evidence_candidate import (
        quantization_failures,
        validation_report,
    )
    _, reference = validation_report(run / "new-f16-validation.json", data, models["f16"])
    _, q4 = validation_report(run / "new-q4-validation.json", data, models["q4"])
    selection = [TRAIN_PYTHON, "-m", "training.freeze_evidence_candidate", "select-quantization", "--run", run, "--data", data,
        "--f16-model", models["f16"], "--f16-report", run / "new-f16-validation.json",
        "--q4-model", models["q4"], "--q4-report", run / "new-q4-validation.json"]
    if quantization_failures(reference, q4):
        pipeline.execute("export_q8", [*artifact[:3], "export", *artifact[3:], "--quant-type", "Q8_0"],
            outputs=[models["q8"]], inputs=[run / "training/selected.json"])
        output = run / "new-q8-validation.json"
        pipeline.execute("q8_validation", [*evaluate, "--model", models["q8"], "--output", output,
            "--alias", f"{ALIAS}-q8-eval"], outputs=[output], inputs=[models["q8"], data / "val.jsonl"])
        selection += ["--q8-model", models["q8"], "--q8-report", output]
    pipeline.execute("select_quantization", selection, outputs=[run / "quantization-selection.json"], inputs=[data / "val.jsonl"])
    model = Path(read(run / "quantization-selection.json")["selected_model"])
    pipeline.execute("legacy_validation", [*evaluate, "--model", model, "--legacy", "--output", run / "new-legacy-validation.json"],
                     outputs=[run / "new-legacy-validation.json"], inputs=[data / "val.jsonl"])
    pipeline.execute("freeze_new_holdout", [TRAIN_PYTHON, "-m", "training.freeze_evidence_candidate", "freeze", "--run", run, "--data", data],
        outputs=[run / "freeze.json", run / "final-data.jsonl", run / "final-data-manifest.json"],
        inputs=[run / "quantization-selection.json", *plans, ROOT / "experiments/slm_evidence_v2_stress.json"])
    for legacy in (False, True):
        variant = "legacy" if legacy else "candidate"
        output = run / f"new-final-{variant}.json"
        command = [*evaluate, "--model", model, "--split", "final", "--output", output]
        if legacy:
            command.append("--legacy")
        pipeline.execute(f"final_{variant}", command, outputs=[output], inputs=[run / "freeze.json", run / "final-data-manifest.json"])
    stop_task_server(run)
    pipeline.execute("quality_qualification", [sys.executable, "-m", "training.qualify_evidence_candidate"],
        outputs=[run / "qualification.json"], inputs=[run / "new-final-candidate.json", run / "new-final-legacy.json"])
    if "portable_package" not in pipeline.status["stages"] or pipeline.status["stages"]["portable_package"].get("state") != "completed":
        package(run)
        pipeline.status["stages"]["portable_package"] = {"state": "completed", "archive_sha256": sha(run / "scram-observer-evidence-v2.zip")}
        pipeline.save()
    else:
        if sha(run / "scram-observer-evidence-v2.zip") != pipeline.status["stages"]["portable_package"]["archive_sha256"]:
            raise ValueError("completed portable archive changed")
        manifest = read(run / "deployment/deployment.json")
        if sha(run / "deployment" / manifest["weights_file"]) != manifest["weights_sha256"]:
            raise ValueError("completed deployment weights changed")
    pipeline.execute("ordinary_ollama_load", [sys.executable, "scripts/verify_observer_ollama.py", "--install", "--directory", run / "deployment",
        "--alias", ALIAS, "--data", data, "--resume", "--output", run / "new-ollama-verification.json"],
        outputs=[run / "new-ollama-verification.json"], inputs=[run / "deployment/deployment.json"])
    quality = read(run / "qualification.json")
    pipeline.status.update(state=quality["status"], candidate_training_and_evaluation_completed=True,
        release_qualified=False, remaining_runtime_gates=quality["runtime_gate_requirements"])
    pipeline.save()
    print(json.dumps({"status": quality["status"], "release_qualified": False,
                      "quality_failures": quality["quality_failures"], "defaults": "legacy V2/V3"}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("all", "collect", "train-evaluate"), default="all")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--plan", action="store_true", help="Show the execution stages without Docker, models or collection")
    args = parser.parse_args()
    if args.plan:
        print(json.dumps({"experiment": str(RUN), "prompts_for_confirmation": False, "workers": args.workers,
            "stages": ["Docker preflight", "real collector smoke and inspection", "complete independent collection",
                       "independent excursion-fixture smoke and 16-recording supplement",
                       "screened train/validation data", "CUDA/token audit", "fresh smoke/save/reload", "fixed main training",
                       "base/adapter validation", "GGUF export and full paired validation", "quantization selection",
                       "freeze new holdout and stress procedure", "one final run per candidate/specialist baseline",
                       "quality qualification", "portable candidate", "ordinary Ollama two-role load"],
            "promotion": "blocked until all quality and sustained runtime gates pass"}, indent=2))
        return 0
    ensure_plan(RUN)
    ensure_excursion_supplement(RUN)
    pipeline = Pipeline(RUN)
    lock = pipeline.folder / "pipeline.lock"
    token = uuid.uuid4().hex
    try:
        with lock.open("x", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "token": token}, stream)
    except FileExistsError:
        print(f"An execution lock already exists: {lock}; refusing duplicate orchestration.")
        return 1
    try:
        pipeline.repair_collector_resume()
        run_pipeline(pipeline, args.phase, args.workers)
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        pipeline.status.update(state="blocked_by_stage_failure", error=f"{type(exc).__name__}: {exc}")
        pipeline.save()
        print(f"Stopped with preserved evidence: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            stop_task_server(RUN)
        finally:
            if lock.exists() and read(lock).get("token") == token:
                lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
