"""
Expert supervisory policy used to LABEL real plant states for the operator
fine-tune. expert(state) -> (setpoints, rationale).

Grounding (no invented numbers):
  * DESIGN targets are the plant's own settled operating point, read from the
    live simulator right after a reset (scripts/reset_plant.reset_and_settle)
    and saved to training/design_point.json by collect_states.py.
  * LIMITS are the SIS interlock's real trip / re-arm thresholds,
    l0_plant.safety.TripLimits() production defaults (pressure trips at
    3000 kPa and re-arms at 2800; level trips at 5% / 95% and re-arms inside
    15-85%).

The expert reads ONLY measured fields (what the operator sees through the basic
process instrumentation) - never `truth`. It emits setpoints, never valves, in
the same JSON schema LLMOperator parses (l3_brain/policy.parse_llm_targets).
`state` may be a PlantState or a plain dict with the same measured keys.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional, Tuple

from l0_plant.safety import TripLimits

HERE = Path(__file__).resolve().parent
DESIGN_POINT_PATH = HERE / "design_point.json"
LIMITS = TripLimits()      # the production SIS thresholds, not a test override

# Back-off bands, in engineering units, placed relative to the real limits.
P_BACKOFF_START = LIMITS.pressure_rearm              # 2800 kPa: start easing at the re-arm line
P_BACKOFF_FULL = LIMITS.pressure_high - 50.0         # 2950 kPa: full back-off 50 kPa short of trip
L_LOW_BACKOFF_START = LIMITS.level_low_rearm + 10.0  # 25 %: start protecting inventory
L_LOW_BACKOFF_FULL = LIMITS.level_low_rearm - 5.0    # 10 %: full back-off 5 % above the 5 % trip
L_HIGH_BACKOFF_START = LIMITS.level_high_rearm - 10.0  # 75 %
L_HIGH_BACKOFF_FULL = LIMITS.level_high_rearm + 5.0    # 90 %: full back-off 5 % below the 95 % trip
P_SP_BACKOFF_KPA = 100.0      # how far the pressure setpoint drops at full back-off
PROD_BACKOFF_FRAC = 0.30      # fraction of design production shed at full back-off
L_SP_BACKOFF_PCT = 10.0       # how far the level setpoint moves at full back-off


def load_design_point(path: Path = DESIGN_POINT_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - run `python -m training.collect_states` first; it reads "
            "the settled operating point from the live simulator and writes it there.")
    return json.loads(path.read_text())


def _get(state, key: str) -> float:
    return float(state[key] if isinstance(state, dict) else getattr(state, key))


def _ramp(x: float, start: float, full: float) -> float:
    """0 at `start`, 1 at `full` (either direction), clipped to [0, 1]."""
    if full == start:
        return 0.0
    return max(0.0, min(1.0, (x - start) / (full - start)))


def expert(state, design: Optional[dict] = None) -> Tuple[dict, str]:
    d = design or load_design_point()
    p0, l0, q0, a0 = d["pressure"], d["level"], d["production"], d["a_comp_pct"]
    hold = {"pressure_sp": None, "level_sp": None, "production_rate_sp": None, "a_comp_sp": None}

    scrammed = bool(state["scrammed"] if isinstance(state, dict) else state.scrammed)
    p, lv = _get(state, "pressure"), _get(state, "level")

    # Rule 1 - interlock tripped: the SIS owns the plant; ask for nothing until it re-arms.
    if scrammed:
        return hold, "Interlock tripped; holding all setpoints until the SIS re-arms."

    # Rule 2 - implausible reading (non-finite, or pressure/level reading <= 0 while the SIS
    # has not tripped, i.e. a dropped-out transmitter): do not steer on bad data, hold.
    if not (math.isfinite(p) and math.isfinite(lv)) or p <= 0.0 or lv <= 0.0:
        return hold, "Pressure or level reading implausible (sensor dropout); holding setpoints."

    sp = {"pressure_sp": p0, "level_sp": l0, "production_rate_sp": q0, "a_comp_sp": a0}
    notes = []

    # Rule 3 - pressure nearing the 3000 kPa trip: from the 2800 kPa re-arm line to 2950 kPa,
    # lower the pressure setpoint (vent more) and shed production (less feed) proportionally.
    wp = _ramp(p, P_BACKOFF_START, P_BACKOFF_FULL)
    if wp > 0:
        sp["pressure_sp"] = p0 - P_SP_BACKOFF_KPA * wp
        sp["production_rate_sp"] = q0 * (1.0 - PROD_BACKOFF_FRAC * wp)
        notes.append(f"pressure {p:.0f} kPa is {LIMITS.pressure_high - p:.0f} kPa from trip, "
                     f"backing off {wp:.0%}")

    # Rule 4 - level nearing the 5 % low trip: raise the level setpoint above design so the
    # level loop closes the product valve and inventory recovers.
    wl = _ramp(lv, L_LOW_BACKOFF_START, L_LOW_BACKOFF_FULL)
    if wl > 0:
        sp["level_sp"] = l0 + L_SP_BACKOFF_PCT * wl
        notes.append(f"level {lv:.1f}% is near the low trip, raising level setpoint")

    # Rule 5 - level nearing the 95 % high trip: lower the level setpoint (drain via product)
    # and shed production (less inflow); the larger of the pressure / level cut wins.
    wh = _ramp(lv, L_HIGH_BACKOFF_START, L_HIGH_BACKOFF_FULL)
    if wh > 0:
        sp["level_sp"] = l0 - L_SP_BACKOFF_PCT * wh
        sp["production_rate_sp"] = min(sp["production_rate_sp"], q0 * (1.0 - PROD_BACKOFF_FRAC * wh))
        notes.append(f"level {lv:.1f}% is near the high trip, lowering level setpoint and production")

    # Rule 6 - composition: always hold the design A-in-purge target; it has no SIS limit,
    # so it is never used as a back-off lever.
    sp = {k: round(v, 1) for k, v in sp.items()}

    # Rule 7 - otherwise: hold the design operating point.
    if not notes:
        return sp, (f"In band (P {p:.0f} kPa, L {lv:.1f}%); holding design targets.")
    text = "; ".join(notes)
    return sp, text[0].upper() + text[1:] + "."
