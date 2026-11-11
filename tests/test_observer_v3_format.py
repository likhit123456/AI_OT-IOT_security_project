"""Parity gate: l2_monitor/observer_v3_format rebuilds EVERY stored v3 train/val/unseen input
byte-for-byte, so the served v3 observer sees exactly what it was trained on. The live
wrapper builds its v3 telemetry through the same module; a live-built window must pass
the same gate (tests/test_observer_wrapper.py and the live sanity run check that)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from l2_monitor import observer_v3_format as V3

DATA = Path(__file__).resolve().parent.parent / "training"
FILES = {s: DATA / f"observer_v3_{s}.jsonl" for s in ("train", "val", "unseen")}
COUNTS = {"train": 3448, "val": 1203, "unseen": 480}


def _examples():
    if not all(f.exists() for f in FILES.values()):
        pytest.skip("v3 observer dataset not built (python -m training.build_observer_dataset)")
    for split, f in FILES.items():
        for line in f.open(encoding="utf-8"):
            yield split, json.loads(line)["messages"]


def rebuild(stored: str) -> str:
    """Re-serialize one stored (or live-built) user turn through observer_v3_format."""
    u = json.loads(stored)
    if u["input"] == "telemetry":
        n = len(u["pressure"])
        samples = [{k: u[k][i] for k in ("pressure", "level", *V3.FLOWS, "a_in_purge", "scrammed",
                                         *(f"{v}_valve_pos" for v in V3.VALVES))} for i in range(n)]
        commanded = [{v: u[f"{v}_valve_cmd"][i] for v in V3.VALVES} for i in range(n)]
        return V3.telemetry_to_json(samples, commanded, u["window_s"])
    return V3.proposals_to_json([V3.proposal_dict(p, p["rationale"], p["provenance"]) for p in u["proposals"]])


def telemetry_key_order() -> list:
    """Key order of a stored v3 telemetry turn (what a live window must reproduce)."""
    for _, m in _examples():
        u = json.loads(m[1]["content"])
        if u["input"] == "telemetry":
            return list(u)
    raise AssertionError("no telemetry example")


def test_every_stored_v3_input_is_rebuilt_byte_for_byte():
    n, per, mismatched, first_bad = 0, {s: 0 for s in FILES}, 0, None
    for split, m in _examples():
        assert m[0]["content"] == V3.SYSTEM_PROMPT
        stored = m[1]["content"]
        rebuilt = rebuild(stored)
        n += 1
        per[split] += 1
        if rebuilt != stored:
            mismatched += 1
            first_bad = first_bad or (stored, rebuilt)
    assert per == COUNTS
    assert mismatched == 0, f"{mismatched}/{n} differ; first:\nstored  {first_bad[0]}\nrebuilt {first_bad[1]}"


def test_stored_telemetry_shapes_and_float_types_match_what_is_served():
    for _, m in _examples():
        u = json.loads(m[1]["content"])
        if u["input"] != "telemetry":
            assert u["cycles"] == len(u["proposals"]) == V3.HISTORY_N
            continue
        assert len(u["pressure"]) == V3.WINDOW_N
        for k, xs in u.items():
            if isinstance(xs, list) and k != "scrammed":
                assert len(xs) == V3.WINDOW_N and all(isinstance(x, float) for x in xs if x is not None), k


def test_assistant_turns_parse_under_the_v3_labels():
    for _, m in _examples():
        v, note = V3.parse(m[2]["content"])
        assert note == "ok" and v["label"] in V3.LABELS
