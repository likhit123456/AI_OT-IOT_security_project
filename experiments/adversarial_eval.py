"""
Red-team evaluation: does SCRAM's SIS-grounded interlock survive a combined
attack that a behavior-only monitor cannot - a scripted, individually-legal
adversarial setpoint sequence (experiments/adversarial_policy.py) PLUS a
frozen pressure sensor (the simulator's own native fault interface,
l0_plant/fault_injection.py), reset to the designed operating point before
every run for reproducibility (scripts/reset_plant.py).

Calibration (see scratchpad calib*.py during development, same container):
  * the simulator's physics run on WALL-CLOCK time, not request count - with
    feeds pinned open and purge/product pinned shut, truth pressure climbs at
    ~3.7-3.8 kPa/s regardless of poll rate.
  * the model hard-clamps truth pressure at 3200 kPa (confirmed empirically
    by driving it there with no enforcer and watching it saturate).
  * under e_stop (forced-safe), truth pressure falls at roughly 15-17 kPa/s;
    level barely moves, so re-arm eligibility (pressure back under the
    re-arm band) typically comes within ~10-15s of a trip in this scenario.

Three conditions, same scenario, same reset point:
  (a) no_enforcer   - nothing watches truth; valves obey the adversarial ask
                      unconditionally.
  (b) monitor_only  - a BehaviorMonitor (monitor/behavior_monitor.py) runs
                      every cycle, watching ONLY measured + requested
                      setpoints, but has no actuation path (consistent with
                      it being a detector, not an interlock) - included to
                      show it never raises a flag, not to show it stopping
                      anything.
  (c) sis_interlock - l0_plant.safety.SafetyInterlock, evaluated on the
                      independent truth channel every cycle, force-safes the
                      plant via e_stop the instant it trips.

Part 4 sweeps the SIS trip threshold and records, per threshold: how much
pressure margin below the physical hard clamp is preserved (damage
prevented) vs. how long the plant sits forced-safe and how much production
that forgoes (useful throughput lost) - the two sides of the same dial.
Written to experiments/frontier_results.csv.

Usage:
    python -m experiments.adversarial_eval               # everything
    python -m experiments.adversarial_eval --compare      # just part 3
    python -m experiments.adversarial_eval --sweep         # just part 4
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from l0_plant import fault_injection as fi
from l0_plant.safety import TripLimits
from l1_stub import L1ControlStub
from monitor.behavior_monitor import BehaviorMonitor
from scripts.reset_plant import reset_and_settle

from experiments.adversarial_policy import AdversarialRamp
from experiments.eval_plant import EvalPlant

REPO_ROOT = Path(__file__).resolve().parent.parent
HARD_CLAMP_KPA = 3200.0          # confirmed empirically; see module docstring
NOMINAL_CLIMB_KPA_S = 3.75        # confirmed empirically, for sizing time budgets


POLL_S = 0.05            # light pace: physics is wall-clock bound, not
                          # iteration-bound (see module docstring), so this
                          # just keeps the request rate sane - it does not
                          # slow down anything that matters.
TRACE_INTERVAL_S = 1.0    # downsampled trace resolution


@dataclass
class RunResult:
    name: str
    enforce: bool
    used_monitor: bool
    cycles_run: int
    wall_s: float
    peak_truth_pressure: float
    tripped: bool
    wall_s_of_trip: Optional[float]
    rearmed: bool
    wall_s_forced_safe: Optional[float]
    monitor_ever_flagged: bool
    monitor_flag_count: int
    monitor_sample_flags: List[str]
    trace: List[dict]        # downsampled, ~1 sample/sec - bounded by run length,
                              # never grows with cycle count


def _setup(enforce: bool, limits: Optional[TripLimits] = None,
           plateau_kpa: float = 2900.0, ramp_cycles: int = 50,
           production_target: float = 140.0):
    """Reset to the designed operating point, clear faults, freeze the
    pressure sensor, and hand back a ready-to-run (plant, policy, l1)."""
    reset_and_settle()
    plant = EvalPlant(enforce=enforce, limits=limits).start()
    fi.clear_all(plant.client)
    state = plant.read()
    fi.freeze(plant.client, "tank_pressure")
    policy = AdversarialRamp(start_kpa=state.pressure, plateau_kpa=plateau_kpa,
                              ramp_cycles=ramp_cycles, production_target=production_target)
    l1 = L1ControlStub()
    return plant, policy, l1, state


def run_condition(name: str, enforce: bool, use_monitor: bool,
                   limits: Optional[TripLimits] = None,
                   max_wall_s: float = 200.0, grace_s_after_trip: float = 20.0,
                   verbose: bool = True) -> RunResult:
    """
    Lightly paced (~20 Hz, POLL_S): the simulator's physics runs on
    wall-clock time, not request count (confirmed empirically - see module
    docstring), so a modest pace keeps the request rate sane without slowing
    anything that actually matters - the L1 PI loops still saturate the
    valves within a few seconds of wall time either way, and everything
    that governs this run (trip detection, the re-arm wait, the cutoffs
    below) is timed off wall-clock, not cycle count.

    Memory is bounded on purpose: no per-cycle list. Only running scalar
    summary stats (peak pressure, trip/re-arm timestamps, monitor flag
    count) plus a trace downsampled to ~1 sample/sec (TRACE_INTERVAL_S),
    so a run of any length holds a small, fixed amount of state.
    """
    plant, policy, l1, state = _setup(enforce=enforce, limits=limits)
    monitor = BehaviorMonitor() if use_monitor else None

    t0 = time.monotonic()
    peak = state.truth["pressure"] if state.truth else state.pressure
    wall_s_of_trip: Optional[float] = None
    wall_s_of_saturation: Optional[float] = None
    rearmed = False
    k = 0
    last_print = t0
    last_trace = t0
    trace: List[dict] = []

    if verbose:
        print(f"\n=== {name} (enforce={enforce}, monitor={use_monitor}) ===", flush=True)

    while True:
        now = time.monotonic()
        elapsed = now - t0
        if elapsed >= max_wall_s:
            break

        k += 1
        targets = policy.decide(state)
        cmd = l1.regulate(targets, state, dt=0.5)
        state = plant.apply(cmd)
        truth_p = state.truth["pressure"] if state.truth else state.pressure
        peak = max(peak, truth_p)

        if monitor is not None:
            monitor.observe(state, targets)

        if plant.safety.tripped and wall_s_of_trip is None:
            wall_s_of_trip = elapsed
            if verbose:
                print(f"  [t={elapsed:6.1f}s, cycle {k}] SIS trip: {plant.safety.reason}  "
                      f"(truth_p={truth_p:.1f} kPa)", flush=True)

        if enforce and plant.safety.tripped:
            # Feeds are already forced shut (EvalPlant.apply); try the
            # deliberate reset once eligible, to show the re-arm path.
            if plant.safety.can_reset(truth_p, state.truth["liquid_level"]):
                if plant.reset_interlock():
                    rearmed = True
                    if verbose:
                        print(f"  [t={elapsed:6.1f}s, cycle {k}] re-armed "
                              f"(truth_p={truth_p:.1f} kPa)", flush=True)
                    break
            if wall_s_of_trip is not None and elapsed - wall_s_of_trip >= grace_s_after_trip:
                break  # didn't re-arm within the grace window; stop anyway

        if not enforce and truth_p >= HARD_CLAMP_KPA - 0.5:
            if wall_s_of_saturation is None:
                wall_s_of_saturation = elapsed
            # Saturated at the physical clamp; a couple more seconds just confirms it.
            elif elapsed - wall_s_of_saturation > 2.0:
                break

        if now - last_trace >= TRACE_INTERVAL_S:
            trace.append({
                "t": round(elapsed, 1), "truth_p": round(truth_p, 2),
                "measured_p": round(state.pressure, 2),
                "purge_v": round(state.purge_valve_pos, 1),
                "f1v": round(state.f1_valve_pos, 1),
                "monitor_flags_so_far": len(monitor.flags) if monitor else 0,
            })
            last_trace = now

        if verbose and now - last_print >= 5.0:
            print(f"  [t={elapsed:6.1f}s, cycle {k}] truth_p={truth_p:.1f} kPa  "
                  f"purge_v={state.purge_valve_pos:.1f}  f1v={state.f1_valve_pos:.1f}", flush=True)
            last_print = now

        time.sleep(POLL_S)

    wall_s = time.monotonic() - t0
    plant.stop()

    wall_s_forced_safe = (
        (wall_s - wall_s_of_trip) if (enforce and wall_s_of_trip is not None) else None
    )

    result = RunResult(
        name=name, enforce=enforce, used_monitor=use_monitor,
        cycles_run=k, wall_s=wall_s, peak_truth_pressure=peak,
        tripped=wall_s_of_trip is not None, wall_s_of_trip=wall_s_of_trip,
        rearmed=rearmed, wall_s_forced_safe=wall_s_forced_safe,
        monitor_ever_flagged=bool(monitor.ever_flagged) if monitor else False,
        monitor_flag_count=len(monitor.flags) if monitor else 0,
        monitor_sample_flags=(monitor.flags[:5] if monitor else []),
        trace=trace,
    )
    if verbose:
        print(f"  cycles={k}  wall={wall_s:.1f}s  peak_truth_p={peak:.1f} kPa  "
              f"tripped={result.tripped}  rearmed={rearmed}  "
              f"monitor_flagged={result.monitor_ever_flagged} "
              f"({result.monitor_flag_count} flags)  trace_samples={len(trace)}", flush=True)
    return result


def run_three_way_comparison() -> List[RunResult]:
    print("#" * 78, flush=True)
    print("# Part 3: no-enforcer vs behavior-monitor-only vs SIS-grounded interlock", flush=True)
    print("#" * 78, flush=True)
    results = []
    for args in (("no_enforcer", False, False), ("monitor_only", False, True),
                 ("sis_interlock", True, False)):
        r = run_condition(*args)
        results.append(r)
        sys.stdout.flush()   # flush per condition so a kill leaves partial progress

    print("\n--- summary ---", flush=True)
    for r in results:
        print(f"{r.name:16s} peak_truth_p={r.peak_truth_pressure:7.1f} kPa  "
              f"tripped={r.tripped!s:5s}  monitor_flagged={r.monitor_ever_flagged!s:5s}", flush=True)
    return results


def run_frontier_sweep(
    thresholds_kpa: Optional[List[float]] = None,
    out_csv: Optional[Path] = None,
) -> Path:
    print("#" * 78, flush=True)
    print("# Part 4: trip-threshold sweep - damage prevented vs throughput lost", flush=True)
    print("#" * 78, flush=True)
    thresholds_kpa = thresholds_kpa or [2800.0, 2850.0, 2900.0, 2950.0, 3000.0, 3100.0]
    out_csv = out_csv or (REPO_ROOT / "experiments" / "frontier_results.csv")

    rows = []
    fieldnames = ["trip_threshold_kpa", "rearm_threshold_kpa", "tripped",
                  "wall_time_to_trip_s", "rearmed", "damage_prevented_kpa",
                  "wall_time_forced_safe_s", "approx_production_lost_kmol"]
    csv_file = open(out_csv, "w", newline="")
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    writer.writeheader()
    csv_file.flush()

    for threshold in thresholds_kpa:
        rearm = max(threshold - 200.0, 2650.0)  # same 200 kPa hysteresis gap as the default limits
        limits = TripLimits(pressure_high=threshold, pressure_rearm=rearm)
        # Generous wall-clock budget: climb to `threshold` at ~3.75 kPa/s plus
        # a grace window to observe the re-arm.
        climb_budget_s = max(60.0, (threshold - 2650.0) / 3.0)
        r = run_condition(f"sweep_{threshold:.0f}", enforce=True, use_monitor=False,
                           limits=limits, max_wall_s=climb_budget_s, grace_s_after_trip=20.0)
        sys.stdout.flush()   # flush per threshold so a kill leaves partial progress

        damage_prevented_kpa = HARD_CLAMP_KPA - threshold
        # Approx production forgone: nominal target rate (kmol/h) held at ~0
        # for the forced-safe window. Small in absolute terms here because
        # this plant's re-arm window is short (~10-15s) - see module docstring.
        nominal_rate_kmol_h = 140.0
        approx_production_lost_kmol = (
            nominal_rate_kmol_h * (r.wall_s_forced_safe / 3600.0)
            if r.wall_s_forced_safe is not None else None
        )

        row = {
            "trip_threshold_kpa": threshold,
            "rearm_threshold_kpa": rearm,
            "tripped": r.tripped,
            "wall_time_to_trip_s": r.wall_s_of_trip,
            "rearmed": r.rearmed,
            "damage_prevented_kpa": damage_prevented_kpa,
            "wall_time_forced_safe_s": r.wall_s_forced_safe,
            "approx_production_lost_kmol": approx_production_lost_kmol,
        }
        rows.append(row)
        writer.writerow(row)   # write + flush per threshold, not just at the end
        csv_file.flush()

    csv_file.close()
    print(f"\nwrote {out_csv}", flush=True)
    print(f"{'threshold':>10} {'damage_prevented':>17} {'forced_safe_s':>13} {'prod_lost_kmol':>14}")
    for row in rows:
        print(f"{row['trip_threshold_kpa']:>10.0f} {row['damage_prevented_kpa']:>17.1f} "
              f"{(row['wall_time_forced_safe_s'] or 0):>13.1f} "
              f"{(row['approx_production_lost_kmol'] or 0):>14.4f}")
    return out_csv


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass  # older Python without reconfigure(); explicit flush=True calls still cover it
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compare", action="store_true", help="run only part 3")
    ap.add_argument("--sweep", action="store_true", help="run only part 4")
    args = ap.parse_args()

    if args.compare and not args.sweep:
        run_three_way_comparison()
    elif args.sweep and not args.compare:
        run_frontier_sweep()
    else:
        run_three_way_comparison()
        run_frontier_sweep()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
