"""
Collect REAL plant states from the live GRFICS simulator for the operator
fine-tune. Nothing here is synthetic: every record is a read of the running
Tennessee-Eastman process.

Diversity comes from varying the CONDITIONS across rollouts - production /
pressure targets, setpoint steps, a pressure excursion, post-reset kicks, and
the simulator's native disturbances (sensor fault modes, valve capacity
degradation, a stuck valve). The plant is reset to its operating point before
every rollout; rollouts run one at a time.

Each rollout is regulated by the L1 stub, driven either by a fixed target
schedule or by the expert policy itself (on-policy states). The SIS interlock
(production limits) stays active throughout; if it trips, the trip is recorded
and the interlock is re-armed once the SIS channel allows.

Recording: the measured state at 2 Hz. Each line holds the measured PlantState
fields (what the operator sees) plus a `truth_eval_only` pressure/level pair
that is NOT used for training. Bounded memory: records stream to a temp file
and are appended to training/states.jsonl only when the rollout completes, so
an interrupted run never leaves a half rollout; completed rollouts are skipped
on rerun.

Also writes training/design_point.json: the settled operating point read from
the sim after the first reset (the expert's design targets).

Usage: python -m training.collect_states [--only NAME] [--list] [--duration-scale 1.0]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from contracts import ControlCommand, SupervisoryTargets
from l0_plant import fault_injection as fi
from l0_plant.plant_service import PlantService
from l1_stub import L1ControlStub
from l3_brain.prompt_format import measured_dict
from scripts.reset_plant import reset_and_settle
from training.expert_policy import DESIGN_POINT_PATH, expert, load_design_point

HERE = Path(__file__).resolve().parent
STATES = HERE / "states.jsonl"
TMP = HERE / "_rollout_tmp.jsonl"
SAMPLE_S = 0.5            # 2 Hz recording
POLL_S = 0.1
L1_DT = 0.5               # same dt the other closed-loop demos give the L1 stub
OPEN_DRIVE = ControlCommand(f1_valve=100.0, f2_valve=100.0, purge_valve=0.0, product_valve=0.0)

# driver: "expert" or "schedule" (list of (t, SupervisoryTargets kwargs)).
# events: (t, raw simulator inputs) - native fault / actuator write-fields only.
ROLLOUTS = [
    dict(name="nominal_expert", duration=130, driver="expert", events=[]),
    dict(name="prod_high_130", duration=130, driver="schedule",
         schedule=[(0, dict(pressure_sp=2750, level_sp=44, production_rate_sp=130, a_comp_sp=47))], events=[]),
    dict(name="prod_low_70", duration=130, driver="schedule",
         schedule=[(0, dict(pressure_sp=2650, level_sp=40, production_rate_sp=70, a_comp_sp=47))], events=[]),
    dict(name="prod_steps", duration=160, driver="schedule",
         schedule=[(0, dict(pressure_sp=2700, level_sp=44, production_rate_sp=100, a_comp_sp=47)),
                   (40, dict(pressure_sp=2700, level_sp=44, production_rate_sp=140, a_comp_sp=47)),
                   (80, dict(pressure_sp=2700, level_sp=44, production_rate_sp=60, a_comp_sp=45)),
                   (120, dict(pressure_sp=2700, level_sp=44, production_rate_sp=110, a_comp_sp=49))], events=[]),
    dict(name="pressure_sp_steps", duration=160, driver="schedule",
         schedule=[(0, dict(pressure_sp=2600, level_sp=50, production_rate_sp=100, a_comp_sp=47)),
                   (55, dict(pressure_sp=2850, level_sp=50, production_rate_sp=100, a_comp_sp=47)),
                   (110, dict(pressure_sp=2700, level_sp=38, production_rate_sp=100, a_comp_sp=47))], events=[]),
    dict(name="pressure_excursion", duration=200, driver="excursion", events=[]),
    dict(name="pressure_sensor_noise", duration=130, driver="expert",
         events=[(0, {"tank_pressure_fault_mode": int(fi.FaultMode.NOISE), "tank_pressure_fault_severity": 40.0})]),
    dict(name="level_drift_high", duration=150, driver="expert",
         events=[(10, {"tank_level_fault_mode": int(fi.FaultMode.DRIFT), "tank_level_fault_severity": 1200.0})]),
    dict(name="level_frozen_feed_moves", duration=130, driver="schedule",
         schedule=[(0, dict(pressure_sp=2700, level_sp=44, production_rate_sp=130, a_comp_sp=47)),
                   (65, dict(pressure_sp=2700, level_sp=44, production_rate_sp=70, a_comp_sp=47))],
         events=[(5, {"tank_level_fault_mode": int(fi.FaultMode.FROZEN), "tank_level_fault_severity": 0.0})]),
    dict(name="sensor_dropouts", duration=130, driver="expert",
         events=[(0, {"analyzer_fault_mode": int(fi.FaultMode.DROPOUT), "analyzer_fault_severity": 0.3}),
                 (0, {"tank_pressure_fault_mode": int(fi.FaultMode.DROPOUT), "tank_pressure_fault_severity": 0.1}),
                 (0, {"product_flow_fault_mode": int(fi.FaultMode.NOISE), "product_flow_fault_severity": 5.0})]),
    dict(name="valve_degradation", duration=130, driver="schedule",
         schedule=[(0, dict(pressure_sp=2700, level_sp=44, production_rate_sp=120, a_comp_sp=47))],
         events=[(5, {"product_cv_scale": 0.6, "f1_cv_scale": 0.7})]),
    dict(name="post_reset_kick", duration=130, driver="expert", kick_s=40.0,
         kick=ControlCommand(f1_valve=60.0, f2_valve=30.0, purge_valve=30.0, product_valve=20.0), events=[]),
    dict(name="stuck_purge_high_prod", duration=150, driver="schedule",
         schedule=[(0, dict(pressure_sp=2800, level_sp=44, production_rate_sp=130, a_comp_sp=47))],
         events=[(0, {"purge_stuck": 1})]),
    # --- rebalancing rollouts: the under-covered regimes ---
    dict(name="pressure_excursion_near_trip", duration=300, driver="excursion",
         drive_until=2965.0, recover_s=25.0, events=[]),
    dict(name="pressure_excursion_partial_feed", duration=300, driver="excursion",
         drive_until=2960.0, recover_s=25.0,
         drive_cmd=ControlCommand(f1_valve=80.0, f2_valve=60.0, purge_valve=0.0, product_valve=20.0),
         events=[]),
    # deliberately drives pressure PAST the 3000 kPa trip; the production SIS trips,
    # forces e_stop, and the tripped states are recorded until the operator resets
    dict(name="sis_trip", duration=480, driver="excursion", drive_until=None, drive_max_s=240.0,
         recover_s=20.0, reset_delay_s=30.0, events=[]),
    dict(name="pressure_dropout_heavy", duration=130, driver="expert",
         events=[(0, {"tank_pressure_fault_mode": int(fi.FaultMode.DROPOUT), "tank_pressure_fault_severity": 0.3})]),
]


class _ExpertBrain:
    def __init__(self, design):
        self.design = design

    def __call__(self, state) -> SupervisoryTargets:
        sp, why = expert(state, self.design)
        return SupervisoryTargets(authority=1.0, rationale=why, **sp)


def write_design_point() -> dict:
    point = reset_and_settle()
    svc = PlantService().start()
    raw = svc.client.read()
    svc.stop()
    d = {"pressure": round(point.pressure, 1), "level": round(point.level, 2),
         "production": round(point.product_flow, 1),
         "a_comp_pct": round(raw["outputs"]["A_in_purge"] * 100.0, 2),
         "valves": {"f1": round(point.f1_valve_pos, 2), "f2": round(point.f2_valve_pos, 2),
                    "purge": round(point.purge_valve_pos, 2), "product": round(point.product_valve_pos, 2)},
         "source": "settled operating point read from the live GRFICS sim after reset"}
    DESIGN_POINT_PATH.write_text(json.dumps(d, indent=1))
    return d


def done_rollouts() -> set:
    if not STATES.exists():
        return set()
    names = set()
    with STATES.open() as f:
        for line in f:
            names.add(json.loads(line)["rollout"])
    return names


def run_rollout(spec: dict, design: dict, scale: float) -> int:
    for attempt in (1, 2):           # first read after a restart can time out
        try:
            reset_and_settle()
            svc = PlantService().start()     # production SIS limits
            state = svc.read()
            break
        except (TimeoutError, OSError):
            if attempt == 2:
                raise
            time.sleep(3)
    l1, brain = L1ControlStub(), _ExpertBrain(design)
    events = sorted(spec.get("events", []), key=lambda e: e[0])
    schedule = spec.get("schedule", [])
    duration = spec["duration"] * scale
    n = 0
    phase, phase_t0 = "drive", 0.0                 # excursion mode only
    trip_t = None
    t0 = time.monotonic()
    last_rec = -1e9
    with TMP.open("w") as out:
        while (t := time.monotonic() - t0) < duration:
            while events and events[0][0] <= t:
                svc.client.write_inputs(events.pop(0)[1])

            if spec["driver"] == "excursion":
                # open-loop push toward (or, with drive_until=None, past) the pressure trip,
                # then hand back to the expert; while the SIS is tripped it overrides the push
                drive_until = spec.get("drive_until", 2930.0)
                if svc.safety.tripped:
                    phase = "tripped"
                elif phase == "tripped":                  # SIS re-armed
                    phase, phase_t0 = "recover", t
                elif phase == "drive" and ((drive_until is not None and state.pressure >= drive_until)
                                           or t - phase_t0 > spec.get("drive_max_s", 90.0)):
                    phase, phase_t0 = "recover", t
                elif phase == "recover" and t - phase_t0 > spec.get("recover_s", 45.0):
                    phase, phase_t0 = "drive", t
                cmd = (spec.get("drive_cmd", OPEN_DRIVE) if phase in ("drive", "tripped")
                       else l1.regulate(brain(state), state, L1_DT))
                label = f"excursion_{phase}"
            elif t < spec.get("kick_s", 0.0):
                cmd, label = spec["kick"], "kick"
            else:
                if spec["driver"] == "expert":
                    targets = brain(state)
                else:
                    kw = [s for ts, s in schedule if ts <= t][-1]
                    targets = SupervisoryTargets(**kw)
                cmd, label = l1.regulate(targets, state, L1_DT), spec["driver"]
            state = svc.apply(cmd)

            # deliberate operator reset, reset_delay_s after the trip; it only succeeds
            # once the SIS channel is back inside the re-arm band
            trip_t = (trip_t if trip_t is not None else t) if svc.safety.tripped else None
            if trip_t is not None and t - trip_t >= spec.get("reset_delay_s", 0.0):
                svc.reset_interlock()
            if t - last_rec >= SAMPLE_S:
                last_rec = t
                rec = {"rollout": spec["name"], "phase": label, "t": round(t, 2),
                       "measured": measured_dict(state),
                       "truth_eval_only": {"pressure": round(state.truth["pressure"], 2),
                                           "level": round(state.truth["liquid_level"], 3)}}
                out.write(json.dumps(rec) + "\n")
                n += 1
            time.sleep(POLL_S)
    fi.clear_all(svc.client)
    svc.stop()
    with STATES.open("a") as dst, TMP.open() as src:
        for line in src:
            dst.write(line)
    TMP.unlink()
    return n


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--duration-scale", type=float, default=1.0)
    a = ap.parse_args()
    if a.list:
        for r in ROLLOUTS:
            print(r["name"], r["duration"], r["driver"])
        return 0
    if not DESIGN_POINT_PATH.exists():
        print("design point:", json.dumps(write_design_point()))
    design = load_design_point()
    done = done_rollouts()
    for spec in ROLLOUTS:
        if (a.only and spec["name"] != a.only) or spec["name"] in done:
            continue
        t = time.monotonic()
        n = run_rollout(spec, design, a.duration_scale)
        print(f"[{spec['name']}] {n} states in {time.monotonic() - t:.0f}s", flush=True)
    print(f"total states in {STATES.name}: {sum(1 for _ in STATES.open())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
