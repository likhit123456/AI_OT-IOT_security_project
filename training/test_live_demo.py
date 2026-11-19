"""Bounded real control-room smoke against the user-started simulator.

No simulator reset or training. One temporary pressure-sensor freeze is cleared
before shutdown; raw observations are saved as integration evidence, not labels
for selecting or tuning the frozen model.
"""
from __future__ import annotations

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
from training.shared_slm_data import sha
from training.train_demo_observer import DESTINATION


def main():
    folder = DESTINATION / ("live-smoke-"+datetime.now().strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(exist_ok=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8765))
    with GrficsClient() as client:
        initial = client.read()
    (folder / "initial-plant.json").write_text(json.dumps(initial, indent=2), encoding="utf-8")
    state = initial["state"]
    if any(state.get(f"{ch}_fault_mode", 0) for ch in fi.CHANNELS):
        raise RuntimeError("Existing sensor faults present; refusing to clear the user's scenario")
    if any(state.get(f"{v}_stuck", False) or state.get(f"{v}_cv_scale", 1) != 1
           for v in ("f1", "f2", "purge", "product")):
        raise RuntimeError("Existing actuator faults present; refusing to clear the user's scenario")
    os.environ.update(settings())
    service = start()
    engine = Engine(brain="baseline", reset=False)
    durations = []
    original = engine._cycle

    def measured_cycle():
        began = time.perf_counter()
        try:
            return original()
        finally:
            durations.append(time.perf_counter()-began)

    engine._cycle = measured_cycle
    web = None
    control_thread = None
    fault_started = fault_cleared = observer_reset = False
    inject_at = clear_at = reset_at = None
    last = {}
    failure = None
    try:
        engine.start()
        control_thread = next(t for t in threading.enumerate() if t.name == "scram-control")
        web = serve(engine)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        began = time.monotonic()
        printed = -30
        with (folder / "snapshots.jsonl").open("x", encoding="utf-8") as snapshots:
            while time.monotonic()-began < 300:
                elapsed = time.monotonic()-began
                if checked_process(service) is None:
                    raise RuntimeError("Recorded model process exited during the test")
                with urllib.request.urlopen("http://127.0.0.1:8765/api/state", timeout=5) as response:
                    last = json.load(response)
                snapshots.write(json.dumps({"elapsed_s": elapsed, "state": last})+"\n")
                snapshots.flush()
                if last.get("status") == "error":
                    raise RuntimeError(last.get("error", "Control room failed"))
                if elapsed > 30 and last.get("status") != "running":
                    raise RuntimeError("Control room did not enter running state")
                if last.get("status") == "running":
                    pressure = last.get("truth", {}).get("pressure")
                    level = last.get("truth", {}).get("level")
                    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (pressure, level)):
                        raise RuntimeError("Invalid independent safety telemetry")
                    if pressure >= 2950 or not 41.5 < level < 70:
                        raise RuntimeError("Plant left the bounded smoke operating envelope")
                    if last.get("history") and last["history"][-1][12]:
                        raise RuntimeError("Safety interlock tripped; ending smoke")
                    if elapsed >= 120 and not fault_started:
                        engine.submit("sensor_fault", channel="tank_pressure", mode="frozen", severity=0)
                        fault_started, inject_at = True, elapsed
                    if elapsed >= 155 and fault_started and not fault_cleared:
                        engine.submit("clear_faults")
                        fault_cleared, clear_at = True, elapsed
                    if elapsed >= 220 and not observer_reset:
                        engine.observer.reset()
                        observer_reset, reset_at = True, elapsed
                if elapsed-printed >= 30:
                    observer = last.get("observer", {})
                    print(json.dumps({"elapsed_s": round(elapsed), "status": last.get("status"),
                        "cycles": last.get("cycles"), "model_calls": observer.get("calls"),
                        "model_failures": observer.get("failures"), "pressure": last.get("measured", {}).get("pressure"),
                        "llm_telemetry": observer.get("llm_telemetry", {}).get("label") if observer.get("llm_telemetry") else None}), flush=True)
                    printed = elapsed
                time.sleep(2)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - save failures after bounded cleanup
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        engine.stop()
        if control_thread:
            control_thread.join(8)
        if fault_started:
            with GrficsClient() as client:
                fi.clear(client, "tank_pressure")
        if hasattr(engine, "svc"):
            engine.svc.stop()
        if web:
            web.shutdown()
            web.server_close()
        stop()
    with GrficsClient() as client:
        final = client.read()
    (folder / "final-plant.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    trace = last.get("observer", {})
    roles = {entry["kind"] for entry in trace.get("recent_requests", []) if entry.get("label")}
    if not failure and (roles != {"proposals", "telemetry"} or trace.get("failures", 1)):
        failure = "Both observer roles did not complete cleanly"
    values = sorted(durations)
    report = {"status": "passed" if failure is None else "failed", "error": failure,
              "duration_s": last.get("t"), "cycles": last.get("cycles"),
              "model_calls": trace.get("calls"), "model_failures": trace.get("failures"),
              "observer_roles": sorted(roles), "inference_process": service,
              "model_process_stopped_after_test": checked_process(service) is None,
              "control_cycle_p95_s": values[math.ceil(.95*len(values))-1] if values else None,
              "control_cycle_samples": len(values), "pressure_freeze_started_s": inject_at,
              "pressure_freeze_cleared_s": clear_at, "observer_reset_s": reset_at,
              "pressure_fault_mode_after": final["state"].get("tank_pressure_fault_mode"),
              "initial_raw_sha256": sha(folder / "initial-plant.json"),
              "final_raw_sha256": sha(folder / "final-plant.json"),
              "simulator_reset": False, "model_training": False, "defaults_changed": False,
              "relative_legacy_benchmark": False, "release_qualified": False,
              "test_app_and_model_stopped": True}
    with (folder / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({"folder": str(folder), **report}), flush=True)
    return 0 if not failure else 1


if __name__ == "__main__":
    raise SystemExit(main())
