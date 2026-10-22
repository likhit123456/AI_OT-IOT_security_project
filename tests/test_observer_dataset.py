"""Fast tests for the v3 observer format and dataset builder (no container, no model)."""
from __future__ import annotations

import json
from collections import Counter

import pytest

from monitor import observer_v2_format as V2
from monitor import observer_v3_format as V3
from training import build_observer_dataset as B

MEAS = {"pressure": 2700.0, "level": 44.18, "f1_flow": 384.0, "f2_flow": 21.2, "purge_flow": 717.9,
        "product_flow": 127.5, "a_in_purge": 0.47, "f1_valve_pos": 31.45, "f2_valve_pos": 13.24,
        "purge_valve_pos": 0.39, "product_valve_pos": 47.03, "scrammed": False}
SMALL = {"train": {"normal": 20, "excursion": 15, "sensor_fault": 15, "actuator_fault": 15},
         "val": {"normal": 6, "excursion": 5, "sensor_fault": 5, "actuator_fault": 5},
         "unseen": {"normal": 5, "excursion": 5, "sensor_fault": 5, "actuator_fault": 5}}


def test_v3_prompt_changes_only_the_telemetry_paragraph():
    v2, v3 = V2.SYSTEM_PROMPT.split("\n"), V3.SYSTEM_PROMPT.split("\n")
    assert len(v2) == len(v3) and [i for i, (a, b) in enumerate(zip(v2, v3)) if a != b] == [1]
    assert "_valve_cmd" in v3[1] and v3[1].startswith('If input is "telemetry"')
    assert V3.LABELS == V2.LABELS and V3.proposals_to_json is V2.proposals_to_json


def test_v3_telemetry_turn_has_long_window_all_flows_and_commanded_valves():
    meas = [{**MEAS, "pressure": 2700.0 + i} for i in range(V3.WINDOW_N)]
    cmd = [{"f1": 31.45, "f2": 13.24, "purge": 0.39, "product": 47.03 + (10 if i > 8 else 0)} for i in range(V3.WINDOW_N)]
    obj = json.loads(V3.telemetry_to_json(meas, cmd, 15.0))
    assert obj["input"] == "telemetry" and obj["window_s"] == 15.0
    assert all(len(obj[k]) == V3.WINDOW_N for k in obj if isinstance(obj[k], list))
    assert {"f2_flow", "f2_valve_pos", "f2_valve_cmd", "product_valve_cmd"} <= set(obj)
    assert obj["product_valve_cmd"][-1] == 57.03 and obj["product_valve_pos"][-1] == 47.03
    assert "truth" not in json.dumps(obj)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("v3")
    report = B.build(seed=1, out_dir=out, counts=SMALL, obs_states=out / "none.jsonl")
    return out, report


def _read(path):
    return [json.loads(line)["messages"] for line in path.open(encoding="utf-8")]


def test_v2_proposal_examples_are_carried_over_byte_for_byte(built):
    out, _ = built
    for split in ("train", "val"):
        v2 = Counter((m[1]["content"], m[2]["content"]) for m in _read(B.V2_DIR / f"observer_{split}.jsonl")
                     if json.loads(m[1]["content"])["input"] == "proposals")   # v2 has a few duplicates; keep them
        v3 = [(m[1]["content"], m[2]["content"]) for m in _read(out / f"observer_v3_{split}.jsonl")
              if json.loads(m[1]["content"])["input"] == "proposals"]
        assert Counter(v3) == v2


def test_builder_emits_valid_jsonl_with_the_requested_telemetry_counts(built):
    out, report = built
    users = {}
    for split in ("train", "val", "unseen"):
        tel = Counter()
        users[split] = set()
        for m in _read(out / f"observer_v3_{split}.jsonl"):
            assert [t["role"] for t in m] == ["system", "user", "assistant"] and m[0]["content"] == V3.SYSTEM_PROMPT
            u = json.loads(m[1]["content"])
            v, note = V3.parse(m[2]["content"])
            assert note == "ok" and (v["score"] == 0.0) == (v["label"] == "normal")
            if u["input"] == "telemetry":
                tel[v["label"]] += 1
                assert len(u["pressure"]) == V3.WINDOW_N and 14.0 <= u["window_s"] <= 20.0   # real recordings: small timing gaps
                users[split].add(m[1]["content"])
        assert dict(tel) == SMALL[split], split
    assert not users["unseen"] & users["train"]               # held-out scenarios never trained on
