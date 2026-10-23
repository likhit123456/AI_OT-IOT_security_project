"""
Control-room chat router: turns an operator's typed command into something the plant acts on.

  valves     "set feed 1 to 50%", "open both feeds to 100%", "close the purge"
             -> a manual valve override (L1 holds the valve; Keel and the SIS still guard it).
             The AI operator never chooses valve positions, so these never go to the model.
  release    "release", "back to auto", "give the valves back" -> drop manual overrides
  setpoints  "lower pressure to 2620", "raise production by 10", "level 50%"
             -> a canonical instruction the operator model follows reliably
  free       anything else: sent to the model verbatim (this is how prompt injection is tried)
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from l3_brain.prompt_format import DESIGN_POINT

_NUM = r"(-?\d+(?:\.\d+)?)"
# order matters: "both feeds" before "feed 1"
_VALVES = (
    (("f1", "f2"), r"\bboth\b[^.,;]*\bfeeds?\b|\bfeeds?\b[^.,;]*\bboth\b|\bfeeds\b(?!\s*[12])|\ball feeds\b"),
    (("f1",), r"\bfeed\s*(?:1|one)\b|\bf1\b|\bfirst feed\b"),
    (("f2",), r"\bfeed\s*(?:2|two)\b|\bf2\b|\bsecond feed\b"),
    (("purge",), r"\bpurge\b"),
    (("product",), r"\bproduct\b(?!ion)"),
)
_RELEASE = r"\b(release|back to auto|auto mode|automatic|give (?:the )?(?:valves? )?(?:control )?back|hand (?:it |them )?back|resume)\b"
_SP = {  # name -> (keyword regex, unit, relative step, plausible absolute range)
    "pressure_sp": (r"\bpressure\b|\bkpa\b", "kPa", 50.0, (2000.0, 3200.0)),
    "production_rate_sp": (r"\bproduction\b|\bthroughput\b|\boutput\b|\bkmol", "kmol/h", 10.0, (40.0, 160.0)),
    "level_sp": (r"\blevel\b", "%", 3.0, (10.0, 90.0)),
    "a_comp_sp": (r"\bcomposition\b|\ba in purge\b|\ba[- ]comp", "mol %", 2.0, (30.0, 70.0)),
}
_LABEL = {"pressure_sp": "pressure", "production_rate_sp": "production", "level_sp": "level",
          "a_comp_sp": "A in purge"}
_DESIGN = {"pressure_sp": DESIGN_POINT["pressure"], "production_rate_sp": DESIGN_POINT["production"],
           "level_sp": DESIGN_POINT["level"], "a_comp_sp": DESIGN_POINT["a_comp_pct"]}
_UP = r"\b(raise|increase|up|higher|more|boost|bump)\b"
_DOWN = r"\b(lower|decrease|reduce|down|less|cut|drop)\b"


def _valve_targets(t: str) -> Optional[Dict[str, float]]:
    found = []
    rest = t
    for names, rx in _VALVES:
        if re.search(rx, rest):
            found.extend(n for n in names if n not in found)
            rest = re.sub(rx, " ", rest)
    if not found:
        return None
    m = re.search(_NUM + r"\s*%", t) or re.search(r"\bto\s+" + _NUM + r"\b", t)
    if m:
        pct = float(m.group(1))
    elif re.search(r"\b(fully|wide open|full|max(?:imum)?|open)\b", t):
        pct = 100.0
    elif re.search(r"\b(close|shut|zero)\b", t):
        pct = 0.0
    else:
        return None
    pct = max(0.0, min(100.0, pct))
    return {v: pct for v in found}


def _setpoints(t: str, current: Dict[str, Optional[float]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for name, (kw, _unit, step, (lo, hi)) in _SP.items():
        km = re.search(kw, t)
        if not km:
            continue
        # look for a number near the keyword (same clause)
        clause = re.split(r"[,;]| and ", t[km.start():])[0] + " " + re.split(r"[,;]| and ", t[:km.end()])[-1]
        by = re.search(r"\bby\s+" + _NUM, clause)
        nums = [float(x) for x in re.findall(_NUM, clause)]
        base = current.get(name) if current.get(name) is not None else _DESIGN[name]
        sign = 1.0 if re.search(_UP, clause) else -1.0 if re.search(_DOWN, clause) else 0.0
        if by:
            val = base + (sign or 1.0) * float(by.group(1))
        elif any(lo <= n <= hi for n in nums):
            val = next(n for n in nums if lo <= n <= hi)
        elif re.search(r"\b(design|nominal|normal)\b", clause):
            val = _DESIGN[name]
        elif sign:
            val = base + sign * step
        else:
            continue
        out[name] = round(max(lo, min(hi, val)), 2)
    return out


def parse(text: str, current: Optional[Dict[str, Optional[float]]] = None) -> dict:
    """Classify an operator command. `current` = the setpoints in force (for relative changes)."""
    t = " " + text.lower().replace("’", "'") + " "
    if re.search(_RELEASE, t):
        return {"kind": "release"}
    valves = _valve_targets(t)
    if valves:
        return {"kind": "valves", "valves": valves}
    if re.search(r"\bhold\b.*\bdesign\b|\bdesign point\b", t):
        sp = dict(_DESIGN)
    else:
        sp = _setpoints(t, current or {})
    if sp:
        # keep the operator's own words (framing matters: that is what an injection is)
        # and append the explicit targets, the form the model follows reliably
        return {"kind": "setpoints", "sp": sp, "note": f"{text.strip()} Targets: {canonical(sp)}"}
    return {"kind": "free"}


def canonical(sp: Dict[str, float]) -> str:
    """The instruction form the operator model follows: explicit target values."""
    units = {k: v[1] for k, v in _SP.items()}
    return "Set " + ", ".join(f"{_LABEL[k]} to {v:g} {units[k]}" for k, v in sp.items()) + "."
