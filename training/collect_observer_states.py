"""
Collect REAL telemetry for the L2 observer fine-tune: the simulator's NATIVE
sensor faults (fault_injection modes on several channels) and NATIVE actuator
faults (stuck valves, valve capacity degradation), with the commanded valve
positions recorded next to the measured state.

training/states.jsonl (collect_states.py) already covers normal operation,
excursions and a few sensor faults, but it never recorded what L1 commanded,
so an actuator fault (commanded != actual) cannot be seen in it. These rollouts
fill that gap and give the observer's unseen test set real, not synthesized,
fault windows.

Each rollout: reset to the operating point, run a fixed setpoint schedule (so
the valves have something to do), switch the fault on at FAULT_ON_S, record at
2 Hz. Records stream to a temp file and are appended to
training/observer_states.jsonl only when the rollout completes; completed
rollouts are skipped on rerun.

Usage: python -m training.collect_observer_states [--only NAME] [--list]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from contracts import SupervisoryTargets
from l0_plant import fault_injection as fi
from l0_plant.plant_service import PlantService
from l1_stub import L1ControlStub
from l3_brain.prompt_format import measured_dict
from scripts.reset_plant import reset_and_settle

HERE = Path(__file__).resolve().parent
OUT = HERE / "observer_states.jsonl"
TMP = HERE / "_observer_rollout_tmp.jsonl"
SAMPLE_S, POLL_S, L1_DT = 0.5, 0.1, 0.5        # same as collect_states.py
DURATION_S, FAULT_ON_S = 75.0, 15.0

# setpoint schedule shared by every rollout: design, then a production step, then
# a pressure step - enough valve motion for a stuck valve to show
SCHEDULE = [(0, dict(pressure_sp=2700, level_sp=44, production_rate_sp=100, a_comp_sp=47)),
            (30, dict(pressure_sp=2700, level_sp=44, production_rate_sp=125, a_comp_sp=47)),
            (50, dict(pressure_sp=2650, level_sp=46, production_rate_sp=90, a_comp_sp=47))]

F = fi.FaultMode


def _sensor(ch: str, mode: fi.FaultMode, sev: float) -> dict:
    return dict(name=f"{ch}_{mode.name.lower()}", kind="sensor_fault", channel=ch, mode=mode.name.lower(),
                inputs={f"{ch}_fault_mode": int(mode), f"{ch}_fault_severity": float(sev)})


def _actuator(valve: str, how: str) -> dict:
    inputs = {f"{valve}_stuck": 1} if how == "stuck" else {f"{valve}_cv_scale": 0.5}
    return dict(name=f"{valve}_{how}", kind="actuator_fault", channel=valve, mode=how, inputs=inputs)


ROLLOUTS = [
    _sensor("tank_pressure", F.FROZEN, 0.0), _sensor("tank_pressure", F.DRIFT, 900.0),
    _sensor("tank_pressure", F.NOISE, 25.0), _sensor("tank_pressure", F.DROPOUT, 0.2),
    _sensor("tank_level", F.FROZEN, 0.0), _sensor("tank_level", F.DRIFT, 900.0),
    _sensor("tank_level", F.NOISE, 0.5), _sensor("tank_level", F.DROPOUT, 0.2),
    _sensor("f1_flow", F.FROZEN, 0.0), _sensor("f1_flow", F.NOISE, 20.0),
    _sensor("product_flow", F.FROZEN, 0.0), _sensor("product_flow", F.DRIFT, 600.0),
    _sensor("purge_flow", F.FROZEN, 0.0), _sensor("purge_flow", F.NOISE, 30.0),
    _sensor("analyzer", F.FROZEN, 0.0), _sensor("analyzer", F.DRIFT, 30.0),
    *(_actuator(v, how) for v in ("f1", "f2", "purge", "product") for how in ("stuck", "cv")),
]


def done_rollouts() -> set:
    if not OUT.exists():
        return set()
    return {json.loads(line)["rollout"] for line in OUT.open() if line.strip()}


def run_rollout(spec: dict) -> int:
    for attempt in (1, 2):                      # first read after a restart can time out
        try:
            reset_and_settle()
            svc = PlantService().start()        # production SIS limits
            state = svc.read()
            break
        except (TimeoutError, OSError):
            if attempt == 2:
                raise
            time.sleep(3)
    l1, n, fault_on = L1ControlStub(), 0, False
    t0, last_rec = time.monotonic(), -1e9
    with TMP.open("w") as out:
        while (t := time.monotonic() - t0) < DURATION_S:
            if not fault_on and t >= FAULT_ON_S:
                svc.client.write_inputs(spec["inputs"])
                fault_on = True
            kw = [s for ts, s in SCHEDULE if ts <= t][-1]
            cmd = l1.regulate(SupervisoryTargets(**kw), state, L1_DT)
            state = svc.apply(cmd)
            if svc.safety.tripped:
                svc.reset_interlock()
            if t - last_rec >= SAMPLE_S:
                last_rec = t
                rec = {"rollout": spec["name"], "kind": spec["kind"], "channel": spec["channel"],
                       "mode": spec["mode"], "fault_active": fault_on, "t": round(t, 2),
                       "measured": measured_dict(state),
                       "commanded": {"f1": round(cmd.f1_valve, 4), "f2": round(cmd.f2_valve, 4),
                                     "purge": round(cmd.purge_valve, 4),
                                     "product": round(cmd.product_valve, 4)},
                       "truth_eval_only": {"pressure": round(state.truth["pressure"], 2),
                                           "level": round(state.truth["liquid_level"], 3)}}
                out.write(json.dumps(rec) + "\n")
                n += 1
            time.sleep(POLL_S)
    fi.clear_all(svc.client)
    svc.client.write_inputs({f"{v}_stuck": 0 for v in ("f1", "f2", "purge", "product")}
                            | {f"{v}_cv_scale": 1.0 for v in ("f1", "f2", "purge", "product")})
    svc.stop()
    with OUT.open("a") as dst, TMP.open() as src:
        for line in src:
            dst.write(line)
    TMP.unlink()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for r in ROLLOUTS:
            print(r["name"])
        return 0
    done = done_rollouts()
    todo = [r for r in ROLLOUTS if (a.only in (None, r["name"])) and r["name"] not in done]
    print(f"{len(todo)} rollouts to run ({len(done)} already done)", flush=True)
    for i, spec in enumerate(todo, 1):
        t = time.monotonic()
        n = run_rollout(spec)
        print(f"[{i}/{len(todo)}] {spec['name']}: {n} records in {time.monotonic() - t:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
