"""
Provenance-gate demonstration: the case physics cannot catch.

The injected command is individually inside every physical limit, so the SIS
interlock never trips on it - only command-origin integrity can tell it apart
from a legitimate command. Each scenario resets the plant to its operating
point first; everything uses first-class interfaces only (the enforcer's
apply() path, and the plant's own write protocol for the out-of-band case).

  A  legit      authorized L1 (signed) issues command C        -> admitted, applied
  B  forged     an unsigned copy of the identical C reaches the
                enforcer                                       -> rejected, last-good held
  C  oob+gate   C written directly to :55555, bypassing the
                enforcer, gate active                          -> detected, last-good re-asserted
  D  oob+nogate same direct write, no gate (legacy)            -> undetected, SIS silent

Usage: python -m experiments.provenance_demo
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from contracts import ControlCommand
from l0_plant.grfics_client import GrficsClient
from l0_plant.plant_service import PlantService
from l0_plant.provenance_gate import CommandSigner, ProvenanceGate
from scripts.reset_plant import reset_and_settle

KEY = b"demo-key-not-a-real-secret"
CYCLES_BEFORE, CYCLES_AFTER, POLL_S = 8, 8, 0.1
OUT = Path(__file__).resolve().parent / "provenance_demo_results.json"


def _valves(raw: dict) -> dict:
    s = raw["state"]
    return {k: round(s[k], 2) for k in
            ("f1_valve_pos", "f2_valve_pos", "purge_valve_pos", "product_valve_pos")}


def run(name: str, with_gate: bool) -> dict:
    sp = reset_and_settle()
    op = ControlCommand(sp.f1_valve_pos, sp.f2_valve_pos, sp.purge_valve_pos, sp.product_valve_pos)
    # Command C: valves moved ~10 points from the operating point - far inside
    # every physical limit and routine-looking.
    cmd_c = ControlCommand(op.f1_valve, op.f2_valve,
                           min(100.0, op.purge_valve + 10.0), max(0.0, op.product_valve - 10.0))
    gate = ProvenanceGate(KEY) if with_gate else None
    svc = PlantService(gate=gate).start()
    signer = CommandSigner(KEY)
    rogue = GrficsClient().connect()
    sign = (lambda c: signer.sign(c)) if with_gate else (lambda c: c)

    out = {"scenario": name, "op_valves": _valves(svc.client.read()),
           "command_C": {k: round(v, 2) for k, v in cmd_c.as_dict().items() if "valve" in k}}
    peak_p, min_l, tripped = 0.0, 100.0, False

    def cycle(cmd):
        nonlocal peak_p, min_l, tripped
        st = svc.apply(cmd)
        peak_p = max(peak_p, st.truth["pressure"]); min_l = min(min_l, st.truth["liquid_level"])
        tripped = tripped or svc.safety.tripped
        time.sleep(POLL_S)
        return st

    for _ in range(CYCLES_BEFORE):
        cycle(sign(op))
    # --- the event under test, at cycle CYCLES_BEFORE ---
    if name.startswith("A"):
        cycle(sign(cmd_c))                                  # authorized path
    elif name.startswith("B"):
        cycle(cmd_c)                                        # unsigned copy of the same C
    else:
        rogue.write_inputs({"f1_valve_sp": cmd_c.f1_valve, "f2_valve_sp": cmd_c.f2_valve,
                            "purge_valve_sp": cmd_c.purge_valve,
                            "product_valve_sp": cmd_c.product_valve})   # bypasses the enforcer
        out["valves_right_after_injection"] = _valves(svc.client.read())
        cycle(sign(op))                                     # enforcer's next legit cycle
    out["valves_after_event_cycle"] = _valves(svc.client.read())
    for _ in range(CYCLES_AFTER):
        cycle(sign(op) if not name.startswith(("A", "B")) else sign(cmd_c if name.startswith("A") else op))
    out["valves_end"] = _valves(svc.client.read())
    out["gate_events"] = [(e.kind, e.detail) for e in (gate.events if gate else [])]
    out["sis_tripped"] = tripped
    out["peak_truth_pressure_kpa"] = round(peak_p, 1)
    out["min_truth_level_pct"] = round(min_l, 2)
    rogue.close(); svc.stop()
    print(json.dumps(out, indent=1), flush=True)
    return out


def main() -> int:
    res = [run("A_legit_via_L1", True), run("B_forged_via_enforcer", True),
           run("C_oob_direct_write_gate", True), run("D_oob_direct_write_no_gate", False)]
    OUT.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
