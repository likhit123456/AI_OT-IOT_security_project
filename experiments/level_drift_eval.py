"""
Instrumentation-fault test: SIS low-level interlock vs. a high-drifting
basic-control-system LEVEL transmitter.

Scenario
--------
The operator asks for a lower, perfectly legal level (level_sp = 30%). The
basic-control level transmitter (`tank_level`, the `measured` view) is put
into the simulator's NATIVE drift mode, which biases `measured` upward. L1
regulates on `measured`, so it sees a level that is high / in-band, keeps the
product valve open, and the TRUE level (the independent safety-grade channel,
`outputs`) keeps falling toward the 5% low-level safety setpoint.

Expected:
  (a) a measured-only monitor (band / rate / setpoint-legality / staleness)
      registers nothing: measured level moves plausibly and stays in-band.
  (b) the SIS interlock, reading the safety-grade channel, trips at 5%.

Only the simulator's native fault interface is used (l0_plant/fault_injection
.drift on `tank_level`); there is no signal-corruption code here. The plant is
reset to its designed operating point before every run.

Physics notes (calibrated against the live container, see ARCHITECTURE.md):
  * the sim runs on wall-clock time; truth level drains at only ~0.014 %/s
    regardless of valve combination, so ~45 min from the 44% design point to
    the 5% trip. Runs are therefore long; progress is flushed incrementally.
  * native DRIFT accumulates `severity` units/hour of upward bias.

Usage:
    python -m experiments.level_drift_eval --pilot 120     # short calibration
    python -m experiments.level_drift_eval                 # three conditions
    python -m experiments.level_drift_eval --only sis_interlock
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from contracts import PlantState, SupervisoryTargets
from l0_plant import fault_injection as fi
from l1_stub import L1ControlStub
from l0_plant.safety import TripLimits
from monitor.behavior_monitor import BehaviorMonitor, MonitorLimits
from scripts.reset_plant import reset_and_settle

from experiments.eval_plant import EvalPlant

REPO_ROOT = Path(__file__).resolve().parent.parent
POLL_S = 0.05
TRACE_INTERVAL_S = 15.0
LEVEL_SP = 30.0                  # legal operator ask, well inside the band
DRIFT_RATE_PCT_PER_HR = 60.0     # native drift severity, % level per hour
# Test-only setpoints. Truth level is physics-capped at ~0.013 %/s, so the
# production 5% trip is ~50 min from the 44% operating point. The structural
# claim (measured-only monitor blind, SIS channel trips) does not depend on
# WHERE the low-level setpoint sits, so the experiment moves it near the
# operating point via TripLimits. The production default (5%) is unchanged.
# The monitor's soft low-level band sits just above the trip, mirroring how
# its pressure ceiling sits just inside the pressure trip.
SIS_LIMITS = TripLimits(level_low=40.0, level_low_rearm=42.0)
MONITOR_LIMITS = MonitorLimits(level_low_pct=42.0)
RECOVERY_WINDOW_S = 600.0        # enforced run: wait this long for re-arm


def decide(state: PlantState) -> SupervisoryTargets:
    """Legal, steady operator ask: hold pressure, reduce level to 30%, hold feeds
    (production/comp = None -> L1 freezes feed valves; the stub's production
    loop would otherwise close feed 1 and let pressure sag, slowing the drain)."""
    return SupervisoryTargets(
        pressure_sp=2700.0, level_sp=LEVEL_SP, production_rate_sp=None,
        a_comp_sp=None, authority=1.0, rationale="reduce level to 30%",
    )


@dataclass
class RunResult:
    name: str
    enforce: bool
    used_monitor: bool
    wall_s: float
    min_truth_level: float
    final_truth_level: float
    sis_tripped: bool
    wall_s_of_trip: Optional[float]
    truth_level_at_trip: Optional[float]
    measured_level_at_trip: Optional[float]
    measured_level_min: float
    measured_level_max: float
    max_pressure_truth: float
    monitor_flagged: Optional[bool]
    monitor_flag_count: Optional[int]
    monitor_sample_flags: List[str]
    trace: List[dict]
    level_stale_evaluated: Optional[int] = None
    level_stale_flagged: Optional[int] = None
    min_truth_after_trip: Optional[float] = None
    rearmed: Optional[bool] = None
    wall_s_to_rearm: Optional[float] = None
    fault: str = "drift"


def run_condition(name: str, enforce: bool, use_monitor: bool,
                  max_wall_s: float = 1200.0, drift_rate: float = DRIFT_RATE_PCT_PER_HR,
                  fault: str = "drift", verbose: bool = True) -> RunResult:
    reset_and_settle()
    plant = EvalPlant(enforce=enforce, limits=SIS_LIMITS).start()
    fi.clear_all(plant.client)
    if fault == "drift":
        fi.drift(plant.client, "tank_level", drift_rate)      # native fault only
    elif fault == "frozen":
        fi.freeze(plant.client, "tank_level")                 # native fault only
    state = plant.read()
    l1 = L1ControlStub()
    monitor = BehaviorMonitor(MONITOR_LIMITS) if use_monitor else None

    t0 = time.monotonic()
    truth_l = state.truth["liquid_level"]
    min_truth, final_truth = truth_l, truth_l
    m_min = m_max = state.level
    peak_p = state.truth["pressure"]
    wall_s_of_trip = truth_at_trip = meas_at_trip = None
    min_after_trip: Optional[float] = None
    rearmed, wall_s_rearm = False, None
    last_trace = last_print = t0 - 1e9
    trace: List[dict] = []
    k = 0
    if verbose:
        print(f"\n=== {name} (enforce={enforce}, monitor={use_monitor}, "
              f"drift={drift_rate}%/h) start truth_L={truth_l:.2f} meas_L={state.level:.2f} ===",
              flush=True)

    while True:
        now = time.monotonic()
        elapsed = now - t0
        if elapsed >= max_wall_s:
            break
        k += 1
        targets = decide(state)
        cmd = l1.regulate(targets, state, dt=0.5)
        state = plant.apply(cmd)
        truth_l = state.truth["liquid_level"]
        final_truth = truth_l
        min_truth = min(min_truth, truth_l)
        m_min, m_max = min(m_min, state.level), max(m_max, state.level)
        peak_p = max(peak_p, state.truth["pressure"])
        if monitor is not None:
            monitor.observe(state, targets)

        if plant.safety.tripped and wall_s_of_trip is None:
            wall_s_of_trip, truth_at_trip, meas_at_trip = elapsed, truth_l, state.level
            if verbose:
                print(f"  [t={elapsed:7.1f}s] SIS trip: {plant.safety.reason}  "
                      f"truth_L={truth_l:.2f} measured_L={state.level:.2f}", flush=True)
        if wall_s_of_trip is not None:
            min_after_trip = truth_l if min_after_trip is None else min(min_after_trip, truth_l)
        if enforce and wall_s_of_trip is not None:
            if plant.safety.can_reset(state.truth["pressure"], truth_l) and plant.reset_interlock():
                rearmed, wall_s_rearm = True, elapsed
                if verbose:
                    print(f"  [t={elapsed:7.1f}s] re-armed, truth_L={truth_l:.2f}", flush=True)
                break
            if elapsed - wall_s_of_trip >= RECOVERY_WINDOW_S:
                break
        if not enforce and wall_s_of_trip is not None:
            break          # truth crossed the hazard line; no need to drain further

        if now - last_trace >= TRACE_INTERVAL_S:
            trace.append({"t": round(elapsed, 1), "truth_L": round(truth_l, 3),
                          "meas_L": round(state.level, 3),
                          "truth_P": round(state.truth["pressure"], 1),
                          "product_v": round(state.product_valve_pos, 1),
                          "flags": len(monitor.flags) if monitor else 0})
            last_trace = now
        if verbose and now - last_print >= 60.0:
            print(f"  [t={elapsed:7.1f}s] truth_L={truth_l:6.2f}  meas_L={state.level:6.2f}  "
                  f"P={state.truth['pressure']:6.0f}  prod_v={state.product_valve_pos:5.1f}  "
                  f"flags={len(monitor.flags) if monitor else '-'}", flush=True)
            last_print = now
        time.sleep(POLL_S)

    wall_s = time.monotonic() - t0
    # leave the plant clean for whoever runs next
    fi.clear_all(plant.client)
    plant.stop()
    res = RunResult(
        name=name, enforce=enforce, used_monitor=use_monitor, wall_s=wall_s,
        min_truth_level=min_truth, final_truth_level=final_truth,
        sis_tripped=wall_s_of_trip is not None, wall_s_of_trip=wall_s_of_trip,
        truth_level_at_trip=truth_at_trip, measured_level_at_trip=meas_at_trip,
        measured_level_min=m_min, measured_level_max=m_max, max_pressure_truth=peak_p,
        monitor_flagged=(monitor.ever_flagged if monitor else None),
        monitor_flag_count=(len(monitor.flags) if monitor else None),
        monitor_sample_flags=(monitor.flags[:5] if monitor else []),
        trace=trace,
        level_stale_evaluated=(monitor.level_stale_evaluated if monitor else None),
        level_stale_flagged=(monitor.level_stale_flagged if monitor else None),
        min_truth_after_trip=min_after_trip, rearmed=rearmed if enforce else None,
        wall_s_to_rearm=wall_s_rearm, fault=fault,
    )
    if verbose:
        print(f"  done: wall={wall_s:.0f}s min_truth_L={min_truth:.2f} "
              f"measured_L range [{m_min:.1f},{m_max:.1f}] peak_P={peak_p:.0f} "
              f"tripped={res.sis_tripped} flags={res.monitor_flag_count}", flush=True)
    return res


CONDITIONS = {
    "no_enforcer": (False, False),
    "monitor_only": (False, True),
    "sis_interlock": (True, False),
}


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot", type=float, help="short monitored+enforced run of N seconds")
    ap.add_argument("--only", choices=list(CONDITIONS))
    ap.add_argument("--controls", action="store_true",
                    help="monitor controls: frozen level (must flag) and healthy (must not)")
    ap.add_argument("--drift", type=float, default=DRIFT_RATE_PCT_PER_HR)
    args = ap.parse_args()

    if args.pilot:
        r = run_condition("pilot", True, True, max_wall_s=args.pilot, drift_rate=args.drift)
        print(json.dumps(r.trace[-3:], indent=1))
        return 0

    if args.controls:
        out = REPO_ROOT / "experiments" / "level_monitor_controls.json"
        res = {}
        for fault in ("frozen", "none"):
            r = run_condition(f"control_{fault}", False, True, max_wall_s=40.0, fault=fault)
            res[fault] = asdict(r)
            print(f"  CONTROL {fault}: flags={r.monitor_flag_count} "
                  f"level_stale evaluated={r.level_stale_evaluated} flagged={r.level_stale_flagged} "
                  f"sample={r.monitor_sample_flags[:2]}", flush=True)
        out.write_text(json.dumps(res, indent=1))
        return 0

    out = REPO_ROOT / "experiments" / "level_drift_results.json"
    results = {}
    if out.exists():
        results = json.loads(out.read_text())
    for name, (enforce, mon) in CONDITIONS.items():
        if args.only and name != args.only:
            continue
        r = run_condition(name, enforce, mon, drift_rate=args.drift)
        results[name] = asdict(r)
        out.write_text(json.dumps(results, indent=1))   # incremental, survives a kill
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
