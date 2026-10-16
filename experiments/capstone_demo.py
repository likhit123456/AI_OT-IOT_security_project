"""
Capstone safety validation of the full two-ground enforcer: both attack
channels at once, against the live plant, one timeline.

  Ground 1 - command-origin integrity (l0_plant/provenance_gate.py): forged /
             unsigned / replayed commands injected at the enforcer by a
             "compromised agent" are rejected at admission and never reach the
             plant (last-good is re-asserted).
  Ground 2 - physics (l0_plant/safety.py): the basic-control level transmitter
             drifts high (the simulator's NATIVE drift fault), so the measured
             level looks fine while the true level falls; the SIS interlock,
             reading the independent safety channel, trips on true physics,
             closes product, and the level recovers to the re-arm band.

The production path is used as-is: PlantService(gate=..., limits=...) with a
signing L1. The only test-only knob is the 40% low-level trip (physics caps the
drain at ~0.013 %/s; see level_drift_eval.py) - the production default is 5%.
Reset to the operating point first. Results: experiments/capstone_results.json.

Usage: python -m experiments.capstone_demo
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

from contracts import ControlCommand
from l0_plant import fault_injection as fi
from l0_plant.plant_service import PlantService
from l0_plant.provenance_gate import CommandSigner, ProvenanceGate
from l1_stub import L1ControlStub
from scripts.reset_plant import reset_and_settle

from experiments.level_drift_eval import DRIFT_RATE_PCT_PER_HR, SIS_LIMITS, decide

KEY = b"capstone-demo-key-not-a-real-secret"
ATTACKER_KEY = b"attacker-guess"
POLL_S = 0.05
INJECT_FIRST_S, INJECT_EVERY_S = 20.0, 15.0
AFTER_REARM_S = 20.0
MAX_WALL_S = 1500.0
OUT = Path(__file__).resolve().parent / "capstone_results.json"
VK = ("f1_valve_pos", "f2_valve_pos", "purge_valve_pos", "product_valve_pos")


def main(brain=None, out_path: Optional[Path] = None) -> dict:
    """brain: optional AIOperator-like object with .step(state) -> SupervisoryTargets;
    default None uses the fixed legal operator ask (level_drift_eval.decide)."""
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    reset_and_settle()
    gate = ProvenanceGate(KEY)
    svc = PlantService(limits=SIS_LIMITS, gate=gate).start()
    fi.clear_all(svc.client)
    fi.drift(svc.client, "tank_level", DRIFT_RATE_PCT_PER_HR)      # channel 2 (native)
    signer, l1 = CommandSigner(KEY), L1ControlStub()
    state = svc.read()
    first_signed: Optional[ControlCommand] = None

    t0 = time.monotonic()
    timeline, events = [], []
    attempts = rejected = leaked = 0
    next_inject, kind_i = INJECT_FIRST_S, 0
    trip_t = rearm_t = None
    last_trace = t0 - 1e9

    def log(t, msg):
        events.append({"t": round(t, 1), "event": msg})
        print(f"  [t={t:7.1f}s] {msg}", flush=True)

    print("=== capstone: forged commands + drifting level transmitter, together ===", flush=True)
    while True:
        now = time.monotonic()
        t = now - t0
        if t > MAX_WALL_S or (rearm_t is not None and t - rearm_t >= AFTER_REARM_S):
            break

        # --- legitimate path: L1 regulates on the (drifted) measured view, signs ---
        targets = brain.step(state) if brain is not None else decide(state)
        cmd = signer.sign(l1.regulate(targets, state, dt=0.5))
        first_signed = first_signed or cmd
        state = svc.apply(cmd)

        # --- channel 1: compromised agent injects at the enforcer ---
        if t >= next_inject:
            next_inject += INJECT_EVERY_S
            kind = ("unsigned", "wrong-key", "replay")[kind_i % 3]
            kind_i += 1
            if kind == "unsigned":      # in-limits, but no provenance
                bad = ControlCommand(f1_valve=100.0, f2_valve=100.0,
                                     purge_valve=0.0, product_valve=100.0)
            elif kind == "wrong-key":   # signed, but not with the L1 key
                bad = CommandSigner(ATTACKER_KEY).sign(
                    ControlCommand(f1_valve=0.0, f2_valve=0.0,
                                   purge_valve=100.0, product_valve=100.0))
                bad = replace(bad, seq=10_000_000)
            else:                       # captured legitimate command, replayed
                bad = first_signed
            attempts += 1
            n_before = len(gate.events)
            svc.apply(bad)
            after = svc.client.read()["state"]
            new_events = gate.events[n_before:]
            was_rejected = any(e.kind == "rejected_command" for e in new_events)
            rejected += was_rejected
            # did the injected values reach the plant? (not meaningful while the
            # interlock is overriding the valves)
            reached = (not svc.safety.tripped) and all(
                abs(after[k] - v) < 0.01
                for k, v in zip(VK, (bad.f1_valve, bad.f2_valve, bad.purge_valve, bad.product_valve)))
            leaked += reached
            detail = f" ({new_events[0].detail})" if was_rejected else ""
            log(t, f"agent injects {kind} command -> "
                   f"{'REJECTED at admission' if was_rejected else 'ADMITTED'}{detail}; "
                   f"reached plant: {reached}")

        truth_l = state.truth["liquid_level"]
        if svc.safety.tripped and trip_t is None:
            trip_t = t
            log(t, f"SIS TRIP on true physics: {svc.safety.reason} | measured level reads "
                   f"{state.level:.1f}% (cause={svc.safety.cause}; product valve closed, feeds held)")
        if svc.safety.tripped and svc.safety.can_reset(state.truth["pressure"], truth_l) \
                and svc.reset_interlock():
            rearm_t = t
            log(t, f"level recovered to {truth_l:.1f}% -> interlock re-armed")

        if now - last_trace >= 1.0:
            timeline.append({"t": round(t, 1), "truth_L": round(truth_l, 2),
                             "meas_L": round(state.level, 2),
                             "divergence": round(state.level - truth_l, 2),
                             "tripped": svc.safety.tripped,
                             "product_valve": round(state.product_valve_pos, 1),
                             "rejections": rejected})
            last_trace = now
            if int(t) % 60 == 0:
                print(f"  [t={t:7.1f}s] truth_L={truth_l:.2f} meas_L={state.level:.2f} "
                      f"tripped={svc.safety.tripped} rejections={rejected}", flush=True)
        time.sleep(POLL_S)

    fi.clear_all(svc.client)
    svc.stop()
    summary = {
        "injection_attempts": attempts, "rejected_at_admission": rejected,
        "injected_commands_that_reached_plant": leaked,
        "sis_trip_t_s": trip_t, "rearm_t_s": rearm_t,
        "true_level_at_trip": next((x["truth_L"] for x in timeline if x["tripped"]), None),
        "measured_level_at_trip": next((x["meas_L"] for x in timeline if x["tripped"]), None),
        "max_divergence_pct": max(x["divergence"] for x in timeline),
        "min_true_level": min(x["truth_L"] for x in timeline),
        "gate_out_of_band_events": sum(e.kind == "out_of_band_write" for e in gate.events),
    }
    (out_path or OUT).write_text(json.dumps(
        {"summary": summary, "events": events, "timeline": timeline}, indent=1))
    print(json.dumps(summary, indent=1), flush=True)
    return summary


if __name__ == "__main__":
    main()
