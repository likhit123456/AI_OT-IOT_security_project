"""Fresh live demo check for code-only review; no training or final-test reuse."""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import threading
import time
import urllib.request
from datetime import datetime

from l0_plant import fault_injection as fi
from l0_plant.grfics_client import GrficsClient
from live.engine import Engine
from live.server import serve
from training.launch_demo_control_room import checked_process, settings, start, stop
from training.shared_slm_data import ROOT, sha
from training.train_demo_observer import DESTINATION


def action(name, run_id, **values):
    request = urllib.request.Request("http://127.0.0.1:8765/api/action",
        data=json.dumps({"action": name, "expected_run_id": run_id, **values}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dropout-only", action="store_true")
    args = parser.parse_args()
    duration, on, off = (100, 30, 65) if args.dropout_only else (240, 65, 100)
    folder = DESTINATION / ("evidence-review-live-"+datetime.now().strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(exist_ok=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8765))
    with GrficsClient() as client:
        initial = client.read()
    if any(initial["state"].get(f"{ch}_fault_mode", 0) for ch in fi.CHANNELS):
        raise RuntimeError("Existing sensor faults present; refusing to clear user scenario")
    if any(initial["state"].get(f"{v}_stuck", False) or initial["state"].get(f"{v}_cv_scale", 1) != 1
           for v in ("f1", "f2", "purge", "product")):
        raise RuntimeError("Existing actuator faults present")
    (folder / "initial.json").write_text(json.dumps(initial, indent=2), encoding="utf-8")
    (folder / "plan.json").write_text(json.dumps({"duration_s": duration, "dropout_on_s": on,
        "dropout_off_s": off, "proposal_injection_on_s": None if args.dropout_only else 130, "proposal_injection_duration_s": 45,
        "model_training": False, "sealed_final_test_read": False,
        "review_code_sha256": sha(ROOT / "monitor/evidence_review.py"),
        "runtime_code_sha256": sha(ROOT / "monitor/observer_runtime.py")}, indent=2), encoding="utf-8")
    os.environ.update(settings())
    process = start()
    engine = Engine(brain="baseline", reset=False)
    cycle_times = []
    original = engine._cycle

    def measured_cycle():
        began = time.perf_counter()
        original()
        cycle_times.append(time.perf_counter()-began)

    engine._cycle = measured_cycle
    engine.start()
    control = next(t for t in threading.enumerate() if t.name == "scram-control")
    server = serve(engine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    events, error, last = [], None, {}
    began, printed = time.monotonic(), -30
    try:
        with (folder / "snapshots.jsonl").open("x", encoding="utf-8") as output:
            while time.monotonic()-began < duration:
                elapsed = time.monotonic()-began
                with urllib.request.urlopen("http://127.0.0.1:8765/api/state", timeout=5) as response:
                    last = json.load(response)
                output.write(json.dumps({"elapsed_s": elapsed, "state": last})+"\n")
                output.flush()
                if checked_process(process) is None:
                    raise RuntimeError("Model process disappeared")
                if last["status"] == "error" or (elapsed > 30 and last["status"] != "running"):
                    raise RuntimeError(last.get("error") or last["status"])
                if last["status"] == "running":
                    truth = last["truth"]
                    if not 41.5 < truth["level"] < 70 or truth["pressure"] >= 2950:
                        raise RuntimeError("Smoke operating envelope exceeded")
                    if last["history"][-1][12]:
                        raise RuntimeError("Safety interlock tripped")
                    if not last.get("observer", {}).get("evidence_review", {}).get("enabled"):
                        raise RuntimeError("Evidence review is not active")
                    done = {event["action"] for event in events}
                    if elapsed >= on and "sensor_fault" not in done:
                        action("sensor_fault", last["run_id"], channel="tank_pressure", mode="dropout", severity=.5)
                        events.append({"action": "sensor_fault", "elapsed_s": elapsed})
                    if elapsed >= off and "clear_faults" not in done:
                        action("clear_faults", last["run_id"])
                        events.append({"action": "clear_faults", "elapsed_s": elapsed})
                    if not args.dropout_only and elapsed >= 130 and "operator_injection" not in done:
                        action("operator_injection", last["run_id"], duration_s=45)
                        events.append({"action": "operator_injection", "elapsed_s": elapsed})
                if elapsed-printed >= 30:
                    observer = last.get("observer", {})
                    review = observer.get("evidence_review", {})
                    print(json.dumps({"elapsed_s": round(elapsed), "status": last["status"],
                        "cycles": last.get("cycles"), "calls": observer.get("calls"),
                        "failures": observer.get("failures"), "proposal_review": review.get("proposals"),
                        "telemetry_review": review.get("telemetry"), "authority": last.get("brain", {}).get("effective")}), flush=True)
                    printed = elapsed
                time.sleep(2)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - preserve test errors after cleanup
        error = f"{type(exc).__name__}: {exc}"
    finally:
        engine.stop()
        control.join(8)
        if any(e["action"] == "sensor_fault" for e in events):
            with GrficsClient() as client:
                fi.clear(client, "tank_pressure")
        if hasattr(engine, "svc"):
            engine.svc.stop()
        server.shutdown()
        server.server_close()
        stop()
    with GrficsClient() as client:
        final = client.read()
    (folder / "final.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    times = sorted(cycle_times)
    report = {"status": "completed" if error is None else "failed", "error": error,
        "events": events, "cycles": last.get("cycles"), "calls": last.get("observer", {}).get("calls"),
        "failures": last.get("observer", {}).get("failures"),
        "cycle_p95_s": times[math.ceil(.95*len(times))-1] if times else None,
        "pressure_fault_cleared": final["state"].get("tank_pressure_fault_mode") == 0,
        "model_sha256": process["sha256"], "servers_stopped": checked_process(process) is None,
        "simulator_reset": False, "model_training": False, "release_qualified": False}
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"folder": str(folder), **report}), flush=True)
    return 0 if error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
