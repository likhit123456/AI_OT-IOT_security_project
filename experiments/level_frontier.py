"""
Low-level trip-threshold sweep under a persistent native drift fault on the
basic-control level transmitter: damage prevented vs. throughput lost.

Under the persistent fault, L1 regulates on a transmitter that reads high, so
after every SIS trip -> product closed -> level recovers -> re-arm, L1 reopens
the product valve and drains the vessel back to the trip point. The SIS thus
cycles trip -> re-arm -> re-trip, and the fraction of time spent in the safed
(tripped) state is the throughput given up.

Per threshold (reset to the operating point first, native fault only):
  * low-level trip = threshold, re-arm = threshold + 2 (same hysteresis as the
    40/42 run in level_drift_eval.py); the behaviour monitor's soft low limit
    is threshold + 2, so it tracks just above the trip it is being compared to.
  * the run continues until WINDOW_S seconds after the FIRST trip (an equal
    limit-cycle window for every threshold; time before the first trip is the
    approach drain and differs by threshold, so it is reported separately).
  * damage prevented = threshold - HAZARD_FLOOR_PCT (level margin preserved
    above the 5% hazard floor); throughput lost = safed_time / window.

Each process runs ONE threshold and appends its row to experiments/level_frontier_results.json.

Usage:
    python -m experiments.level_frontier --threshold 35   # one per process, appends
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from l0_plant import fault_injection as fi
from l0_plant.safety import TripLimits
from l1_stub import L1ControlStub
from monitor.behavior_monitor import BehaviorMonitor, MonitorLimits
from scripts.reset_plant import reset_and_settle

from experiments.eval_plant import EvalPlant
from experiments.level_drift_eval import DRIFT_RATE_PCT_PER_HR, decide

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "experiments" / "level_frontier_results.json"
HAZARD_FLOOR_PCT = 5.0
HYSTERESIS_PCT = 2.0
WINDOW_S = 800.0          # ~2 trip/re-arm cycles at ~340 s per cycle
POLL_S = 0.05
TRACE_INTERVAL_S = 1.0
START_LEVEL_PCT = 44.18
SLOW_DRAIN_PCT_S = 0.010  # conservative (measured ~0.0128) for the wall-clock guard


def run_threshold(threshold: float, window_s: float = WINDOW_S) -> dict:
    reset_and_settle()
    limits = TripLimits(level_low=threshold, level_low_rearm=threshold + HYSTERESIS_PCT)
    plant = EvalPlant(enforce=True, limits=limits).start()
    fi.clear_all(plant.client)
    fi.drift(plant.client, "tank_level", DRIFT_RATE_PCT_PER_HR)   # native fault only
    state = plant.read()
    l1 = L1ControlStub()
    monitor = BehaviorMonitor(MonitorLimits(level_low_pct=threshold + HYSTERESIS_PCT))

    max_wall = (START_LEVEL_PCT - threshold) / SLOW_DRAIN_PCT_S + window_s + 300.0
    t0 = last = time.monotonic()
    t_first_trip: Optional[float] = None
    safed_s = 0.0
    trips = rearms = 0
    was_tripped = False
    min_truth = state.truth["liquid_level"]
    peak_p = state.truth["pressure"]
    last_print = last_trace = t0
    trace: List[dict] = []     # downsampled ~1/s; bounded by run length, never per-cycle
    print(f"\n=== threshold {threshold:.0f}% (re-arm {threshold + HYSTERESIS_PCT:.0f}%, "
          f"window {window_s:.0f}s after first trip) ===", flush=True)

    while True:
        now = time.monotonic()
        elapsed = now - t0
        if elapsed >= max_wall:
            print("  wall-clock guard hit before the window closed", flush=True)
            break
        if t_first_trip is not None and elapsed - t_first_trip >= window_s:
            break

        targets = decide(state)
        cmd = l1.regulate(targets, state, dt=0.5)
        state = plant.apply(cmd)
        monitor.observe(state, targets)
        truth_l = state.truth["liquid_level"]
        min_truth = min(min_truth, truth_l)
        peak_p = max(peak_p, state.truth["pressure"])

        step = time.monotonic() - last
        last = time.monotonic()
        if plant.safety.tripped:
            if not was_tripped:
                trips += 1
                if t_first_trip is None:
                    t_first_trip = elapsed
                print(f"  [t={elapsed:7.1f}s] trip #{trips}: {plant.safety.reason}  "
                      f"(measured_L={state.level:.1f})", flush=True)
            if t_first_trip is not None:
                safed_s += step
            was_tripped = True
            if plant.safety.can_reset(state.truth["pressure"], truth_l) and plant.reset_interlock():
                rearms += 1
                was_tripped = False
                print(f"  [t={elapsed:7.1f}s] re-arm #{rearms} (truth_L={truth_l:.2f})", flush=True)
        if now - last_trace >= TRACE_INTERVAL_S:
            trace.append({"t": round(elapsed, 1), "truth_L": round(truth_l, 3),
                          "meas_L": round(state.level, 3),
                          "tripped": plant.safety.tripped})
            last_trace = now
        if now - last_print >= 60.0:
            print(f"  [t={elapsed:7.1f}s] truth_L={truth_l:6.2f} meas_L={state.level:6.2f} "
                  f"tripped={plant.safety.tripped} safed={safed_s:.0f}s", flush=True)
            last_print = now
        time.sleep(POLL_S)

    total = time.monotonic() - t0
    fi.clear_all(plant.client)
    plant.stop()
    window_actual = (total - t_first_trip) if t_first_trip is not None else 0.0
    row = {
        "threshold_pct": threshold,
        "rearm_pct": threshold + HYSTERESIS_PCT,
        "damage_prevented_pct": threshold - HAZARD_FLOOR_PCT,
        "time_to_first_trip_s": t_first_trip,
        "window_s": window_actual,
        "trips": trips,
        "rearms": rearms,
        "safed_s_in_window": safed_s,
        "throughput_lost_frac": (safed_s / window_actual) if window_actual > 0 else None,
        "min_truth_level": min_truth,
        "max_truth_pressure": peak_p,
        "monitor_flag_count": len(monitor.flags),
        "monitor_sample_flags": monitor.flags[:3],
        "level_stale_evaluated": monitor.level_stale_evaluated,
        "level_stale_flagged": monitor.level_stale_flagged,
        "trace": trace,
    }
    print(f"  done: trips={trips} rearms={rearms} safed={safed_s:.0f}s/"
          f"{window_actual:.0f}s lost={row['throughput_lost_frac']} "
          f"monitor_flags={len(monitor.flags)}", flush=True)
    return row


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threshold", type=float, required=True,
                    help="ONE threshold per process; the row is APPENDED to the results file")
    ap.add_argument("--window", type=float, default=WINDOW_S)
    args = ap.parse_args()

    row = run_threshold(args.threshold, args.window)
    rows: List[dict] = json.loads(OUT.read_text()) if OUT.exists() else []
    rows.append(row)                                    # append, never overwrite
    OUT.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
