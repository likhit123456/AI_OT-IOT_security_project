"""Fast tests for the L2 observer prompt format and dataset builder (no container, no model)."""
from __future__ import annotations

import json
import re
from collections import Counter

from monitor import observer_prompt_format as F
from monitor.sequential_monitor import INJECTION_PATTERNS
from training import build_observer_dataset as B

MEAS = {"pressure": 2700.0, "level": 44.18, "f1_flow": 384.0, "f2_flow": 21.2, "purge_flow": 717.9,
        "product_flow": 127.5, "a_in_purge": 0.47, "b_in_purge": 0.14, "c_in_purge": 0.39,
        "f1_valve_pos": 31.45, "f2_valve_pos": 13.24, "purge_valve_pos": 0.39, "product_valve_pos": 47.03,
        "scrammed": False}
SP = {"pressure_sp": 2700.6, "level_sp": 44.18, "production_rate_sp": 100.0, "a_comp_sp": 47.0}


def test_proposal_turn_round_trips_and_caps_history():
    hist = [{**SP, "pressure_sp": 2700.0 + i} for i in range(12)]
    obj = json.loads(F.proposal_to_json(SP, " holding design targets ", hist, 2701.234, 44.1789))
    assert obj["input"] == "proposal" and obj["setpoints"] == SP
    assert obj["rationale"] == "holding design targets"
    assert len(obj["recent_setpoints"]) == F.HISTORY_N and obj["recent_setpoints"][-1]["pressure_sp"] == 2711.0
    assert obj["measured"] == {"pressure": 2701.2, "level": 44.18}
    assert "provenance" not in obj


def test_telemetry_turn_has_stats_and_commanded_vs_actual():
    meas = [{**MEAS, "pressure": 2700.0 + i} for i in range(60)]
    cmd = [{"f1": 31.45, "f2": 13.24, "purge": 0.39, "product": 47.03 + (10 if i > 30 else 0)} for i in range(60)]
    obj = json.loads(F.telemetry_to_json(meas, cmd))
    assert obj["input"] == "telemetry" and obj["window_s"] == 29.5
    p = obj["channels"]["pressure"]
    assert (p["first"], p["last"], p["min"], p["max"]) == (2700.0, 2759.0, 2700.0, 2759.0)
    assert abs(p["slope"] - 2.0) < 1e-6                      # 1 kPa per 0.5 s sample
    assert obj["valves"]["product"]["max_gap"] == 10.0 and obj["valves"]["f1"]["max_gap"] == 0.0
    assert set(obj["channels"]) == set(F.CHANNELS) and "truth" not in json.dumps(obj)


def test_parse_is_tolerant_but_strict_on_labels():
    v, note = F.parse('```json\n{"label": "sensor_fault", "score": 0.7, "reason": "Level frozen."}\n```')
    assert note == "ok" and v == {"label": "sensor_fault", "score": 0.7, "reason": "Level frozen."}
    assert F.parse('{"label": "excursion", "score": 3, "reason": ""}')[0]["score"] == 1.0
    assert F.parse('{"label": "provenance_violation", "score": 0.9}')[0] is None    # dropped class
    assert F.parse('{"label": "normal", "score": "high"}')[0] is None
    assert F.parse("no json here")[0] is None
    assert json.loads(F.verdict_to_json("normal", 0.8, "fine"))["score"] == 0.0


def test_training_injection_phrases_are_ones_the_rule_monitor_knows():
    pats = [re.compile(p, re.I) for p, _ in INJECTION_PATTERNS]
    for phrase in B.INJECTION_TRAIN:
        assert any(p.search(phrase) for p in pats), phrase


def test_builder_emits_valid_balanced_jsonl(tmp_path):
    c = B.build(per_class=30, n_val=10, n_unseen=10, seed=1, out_dir=tmp_path,
                obs_states=tmp_path / "none.jsonl")
    assert c["train"]["by_label"] == {lab: 30 for lab in F.LABELS}
    assert c["val"]["by_label"] == {lab: 10 for lab in F.LABELS}
    assert set(c["unseen"]["by_label"]) == set(F.LABELS)
    users = {}
    for split in ("train", "val", "unseen"):
        labels = Counter()
        users[split] = set()
        for line in (tmp_path / f"observer_{split}.jsonl").open():
            m = json.loads(line)["messages"]
            assert [t["role"] for t in m] == ["system", "user", "assistant"]
            assert m[0]["content"] == F.system_prompt()
            assert json.loads(m[1]["content"])["input"] in ("proposal", "telemetry")
            v, note = F.parse(m[2]["content"])
            assert note == "ok" and (v["score"] == 0.0) == (v["label"] == "normal")
            labels[v["label"]] += 1
            users[split].add(m[1]["content"])
        assert "provenance_violation" not in labels
    assert not users["unseen"] & users["train"]                # held-out scenarios never trained on
