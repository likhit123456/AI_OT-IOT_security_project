"""Parity gate: monitor/observer_v2_format rebuilds EVERY stored v2 training/val input
byte-for-byte, so the served v2 observer sees exactly what it was trained on."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from monitor import observer_v2_format as V2

DATA = Path(__file__).resolve().parent.parent / "training" / "observer_v2"
FILES = [DATA / "observer_train.jsonl", DATA / "observer_val.jsonl"]


def _examples():
    if not all(f.exists() for f in FILES):
        pytest.skip("v2 observer dataset not present")
    for f in FILES:
        for line in f.open(encoding="utf-8"):
            yield json.loads(line)["messages"]


def _rebuild(user: dict) -> str:
    if user["input"] == "telemetry":
        n = len(user["pressure"])
        samples = [{k: user[k][i] for k in (*V2.TELEMETRY_FIELDS, "scrammed")} for i in range(n)]
        return V2.telemetry_to_json(samples, user["window_s"])
    return V2.proposals_to_json([V2.proposal_dict(p, p["rationale"], p["provenance"])
                                 for p in user["proposals"]])


def test_every_stored_input_is_rebuilt_byte_for_byte():
    n = mismatched = 0
    first_bad = None
    for m in _examples():
        assert m[0]["content"] == V2.SYSTEM_PROMPT
        stored = m[1]["content"]
        rebuilt = _rebuild(json.loads(stored))
        n += 1
        if rebuilt != stored:
            mismatched += 1
            first_bad = first_bad or (stored, rebuilt)
    assert n == 3924 + 1261
    assert mismatched == 0, f"{mismatched}/{n} differ; first:\nstored  {first_bad[0]}\nrebuilt {first_bad[1]}"


def test_live_floats_serialize_like_the_stored_numbers():
    # live readings are floats; the dataset must never hold a bare int where a float is
    # served (json would print 0 vs 0.0), and window shapes must match what we serve
    for m in _examples():
        u = json.loads(m[1]["content"])
        if u["input"] == "telemetry":
            assert len(u["pressure"]) == V2.WINDOW_N
            assert all(isinstance(x, float) for k in V2.TELEMETRY_FIELDS for x in u[k] if x is not None)
        else:
            assert u["cycles"] == len(u["proposals"]) == V2.HISTORY_N
            assert all(isinstance(p[k], float) for p in u["proposals"] for k in V2.SETPOINT_KEYS
                       if p[k] is not None)


def test_assistant_turns_parse_under_the_v2_labels():
    for m in _examples():
        v, note = V2.parse(m[2]["content"])
        assert note == "ok" and v["label"] in V2.LABELS
