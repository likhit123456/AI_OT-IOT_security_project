"""Measured-only arithmetic for the next observer experiment (no fault decisions).

Kept separate from the deployed and frozen model prompts. Every statistic uses
the complete rounded wire history, never simulator truth, labels or recording IDs.
"""
from __future__ import annotations

import math
from itertools import groupby, pairwise

from monitor import observer_v3_format as V3

CHANNELS = ("pressure", "level", *V3.FLOWS, "a_in_purge")
NUMERIC_FIELDS = (*CHANNELS, *(f"{v}_valve_{suffix}" for v in V3.VALVES for suffix in ("pos", "cmd")))
FIELDS = {"input", "window_s", "scrammed", *NUMERIC_FIELDS}
VERSION = "measured-evidence-v2"


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def measured_evidence(data):
    """Validate a full v3 wire window and summarize without inferring fault labels.

Equal runs and zeros describe observations; either can occur during normal
operation. Gaps are percentage points, not relative percent. Delta and last gap
are signed. Equality uses the wire values, which already underwent rounding.
"""
    if not isinstance(data, dict) or set(data) != FIELDS or data["input"] != "telemetry":
        raise ValueError("expected exactly the measured v3 telemetry fields")
    duration = data["window_s"]
    if not _number(duration) or duration <= 0:
        raise ValueError("window_s must be finite and positive")
    for field in (*NUMERIC_FIELDS, "scrammed"):
        values = data[field]
        if not isinstance(values, list) or len(values) != V3.WINDOW_N:
            raise ValueError(f"{field}: expected {V3.WINDOW_N} aligned samples")
        valid = all(isinstance(x, bool) for x in values) if field == "scrammed" else all(_number(x) for x in values)
        if not valid:
            raise ValueError(f"{field}: invalid sample type or non-finite value")

    def rounded(value):
        if not math.isfinite(value):
            raise ValueError("non-finite derived evidence")
        return round(value, 4) if value else 0.0

    channels = {}
    for field in CHANNELS:
        xs = data[field]
        channels[field] = [rounded(max(xs) - min(xs)), rounded(xs[-1] - xs[0]),
                           rounded(max(abs(b-a) for a, b in pairwise(xs))),
                           xs.count(0), max(sum(1 for _ in run) for _, run in groupby(xs))]
    gaps = {}
    for valve in V3.VALVES:
        values = [a-c for a, c in zip(data[f"{valve}_valve_pos"], data[f"{valve}_valve_cmd"])]
        gaps[valve] = [rounded(values[-1]), rounded(max(abs(x) for x in values))]
    return {"version": VERSION,
            "channel_columns": ["range", "delta", "max_adjacent_change", "zero_samples", "longest_equal_run_samples"],
            "channels": channels, "valve_gap_columns": ["last_actual_minus_command_pp", "max_abs_gap_pp"],
            "valve_gaps": gaps}


def evidence_messages(kind, user):
    """One reusable experimental prompt builder; no deployed runtime is switched.

The original full history stays intact. Proposal messages remain slm-v1.
"""
    import json

    from monitor.observer_runtime import task_messages

    messages = task_messages(kind, user, "", include_identity=False)
    if kind == "telemetry":
        payload = json.loads(messages[1]["content"])
        payload["measured_evidence"] = measured_evidence(payload["data"])
        messages[1]["content"] = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        messages[0]["content"] += (
            "\nmeasured_evidence contains arithmetic from the complete measured history, not fault labels. "
            "Range, delta and adjacent change use the channel's units; valve gaps use percentage points. "
            "Equal runs count rounded samples, not elapsed seconds. A zero, equal run or gap alone does not "
            "prove a fault. Cite observed evidence without inventing numerical values or causal facts.")
    return messages
