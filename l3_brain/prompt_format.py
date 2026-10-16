"""
The operator prompt format - ONE definition shared by training and serving.

training/build_dataset.py builds every fine-tune example with these functions,
and l3_brain/policy.LLMOperator serves the model with the same ones, so the
model is served exactly what it was trained on:

  system = operator role + the plant's real design point + SIS limits + output contract
  user   = state_to_json(state): measured PlantState fields as JSON (a_in_purge
           as a mole fraction, values rounded to 4 dp, never `truth`)

Change the format here and nowhere else; a model trained on the old format must
be retrained.
"""

from __future__ import annotations

import json

from l0_plant.safety import TripLimits

# Settled operating point read from the live GRFICS sim right after a reset
# (training/collect_states.py writes it to training/design_point.json;
# tests/test_llm_operator.py checks these match that file).
DESIGN_POINT = {"pressure": 2700.6, "level": 44.18, "production": 100.0, "a_comp_pct": 47.0}

MEASURED_KEYS = ("pressure", "level", "f1_flow", "f2_flow", "purge_flow", "product_flow",
                 "a_in_purge", "b_in_purge", "c_in_purge", "f1_valve_pos", "f2_valve_pos",
                 "purge_valve_pos", "product_valve_pos", "scrammed")


def measured_dict(state) -> dict:
    """Measured fields of a PlantState (or an already-recorded dict), rounded."""
    get = (lambda k: state[k]) if isinstance(state, dict) else (lambda k: getattr(state, k))
    return {k: (bool(get(k)) if k == "scrammed" else round(float(get(k)), 4)) for k in MEASURED_KEYS}


def state_to_json(state) -> str:
    return json.dumps(measured_dict(state))


def system_prompt(design: dict = DESIGN_POINT, lim: TripLimits = TripLimits()) -> str:
    return (
        "You are the supervisory operator for a Tennessee-Eastman reactor. You choose SETPOINTS; a "
        "regulatory controller moves the valves and an independent safety interlock (SIS) sits "
        "underneath and overrides you at its trip limits. Never choose valve positions.\n"
        f"Design operating point: pressure {design['pressure']:.1f} kPa, level {design['level']:.2f} %, "
        f"production {design['production']:.1f} kmol/h, A in purge {design['a_comp_pct']:.1f} mol %.\n"
        f"SIS limits: pressure trips at {lim.pressure_high:.0f} kPa (re-arms at {lim.pressure_rearm:.0f}); "
        f"level trips at {lim.level_low:.0f} % / {lim.level_high:.0f} % (re-arms inside "
        f"{lim.level_low_rearm:.0f}-{lim.level_high_rearm:.0f} %).\n"
        "Hold the design targets; back off toward safety as pressure or level nears its limit. If the "
        "interlock has tripped or a reading is implausible, hold (null setpoints).\n"
        "The user message is the measured plant state as JSON (a_in_purge is a mole fraction). "
        "Output ONLY a JSON object:\n"
        '{"pressure_sp": <kPa|null>, "level_sp": <%|null>, "production_rate_sp": <kmol/h|null>, '
        '"a_comp_sp": <mol %|null>, "rationale": "<one sentence>"}'
    )
