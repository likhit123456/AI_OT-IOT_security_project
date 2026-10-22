"""
The v3 L2 observer prompt format - ONE definition shared by training
(training/build_observer_dataset.py) and, once v3 is trained, serving.

v3 changes only the TELEMETRY side of v2 (monitor/observer_v2_format.py):
  * a longer window: WINDOW_N samples at SAMPLE_S spacing (~15 s, v2: 6 samples
    ~2.6 s), so slow drifts are visible
  * every flow (v2 had no feed 2) and, per valve, the actual position next to the
    position L1 commanded (<valve>_valve_pos / <valve>_valve_cmd) - the gap is
    what an actuator fault looks like, and v2 could not see it
The PROPOSALS turn, its system-prompt paragraph and the 7 labels are v2's,
unchanged: v2's proposal-side examples are reused verbatim.

Measured fields and L1's own commands only, never the SIS channel.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence

from monitor import observer_v2_format as V2

LABELS = V2.LABELS
ATTACK_LABELS = V2.ATTACK_LABELS
PROPOSAL_LABELS = V2.PROPOSAL_LABELS
SETPOINT_KEYS = V2.SETPOINT_KEYS
HISTORY_N = V2.HISTORY_N
OPERATOR_CHAIN, INGESTED_TEXT = V2.OPERATOR_CHAIN, V2.INGESTED_TEXT
proposal_dict = V2.proposal_dict
proposals_to_json = V2.proposals_to_json          # proposals turn: v2 verbatim
parse = V2.parse

WINDOW_N = 16                  # telemetry samples per window
SAMPLE_S = 1.0                 # spacing -> a 15 s window
VALVES = ("f1", "f2", "purge", "product")
FLOWS = ("f1_flow", "f2_flow", "purge_flow", "product_flow")

_V2_TELEMETRY_LINE = V2.SYSTEM_PROMPT.split("\n")[1]
_V3_TELEMETRY_LINE = (
    'If input is "telemetry": a window of the most recent MEASURED plant readings, one sample per second '
    'over window_s seconds, oldest first: pressure, level, the four flows, a_in_purge (mole fraction), and for '
    'each valve its actual position (<valve>_valve_pos) next to the position the controller commanded '
    '(<valve>_valve_cmd), plus the scrammed flag. Classify it as one of: normal; excursion (the plant is being '
    'pushed toward a limit); sensor_fault (a reading is frozen, drifting, noisy or dropping out in a way the '
    'other readings do not support); actuator_fault (a valve is not responding as commanded: its actual '
    'position does not follow the commanded one, or its flow does not follow its opening).')
assert _V2_TELEMETRY_LINE.startswith('If input is "telemetry"')
SYSTEM_PROMPT = V2.SYSTEM_PROMPT.replace(_V2_TELEMETRY_LINE, _V3_TELEMETRY_LINE)


def _r(x, nd: int):
    return None if x is None else round(x, nd)


def telemetry_to_json(samples: Sequence[dict], commanded: Sequence[dict], window_s: float) -> str:
    """samples: the last WINDOW_N measured dicts, oldest first (pressure, level, the four
    flows, a_in_purge as a mole fraction, <valve>_valve_pos, scrammed). commanded: the
    matching {f1, f2, purge, product} positions L1 sent. window_s: first -> last sample."""
    body = {"input": "telemetry", "window_s": round(window_s, 1)}
    for k in ("pressure", "level", *FLOWS):
        body[k] = [_r(s[k], 2) for s in samples]
    body["a_in_purge"] = [_r(s["a_in_purge"], 3) for s in samples]
    for v in VALVES:
        body[f"{v}_valve_pos"] = [_r(s[f"{v}_valve_pos"], 2) for s in samples]
        body[f"{v}_valve_cmd"] = [_r(c[v], 2) for c in commanded]
    body["scrammed"] = [bool(s["scrammed"]) for s in samples]
    return json.dumps(body)


def messages(user: str, assistant: Optional[str] = None) -> list:
    m = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    if assistant is not None:
        m.append({"role": "assistant", "content": assistant})
    return m


def verdict_to_json(label: str, score: float, reason: str) -> str:
    """Same key order and score convention as v2's stored assistant turns."""
    if label not in LABELS:
        raise ValueError(f"unknown label {label!r}")
    return json.dumps({"label": label, "score": 0.0 if label == "normal" else round(float(score), 2),
                       "reason": reason.strip()[:200]})
