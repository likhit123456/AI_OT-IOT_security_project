"""Arithmetic, provenance boundaries and complete-history checks for evidence v2."""
import copy
import json

import pytest

from monitor.telemetry_evidence import (
    NUMERIC_FIELDS,
    evidence_messages,
    measured_evidence,
)
from training.audit_slm_evidence import review_issues, token_count
from training.recover_slm_provenance import fingerprints, source_index


def window():
    data = {field: [10.0]*16 for field in NUMERIC_FIELDS}
    data.update(input="telemetry", window_s=15.0, scrammed=[False]*16)
    return data


def test_measured_arithmetic_distinguishes_signed_delta_range_steps_and_runs():
    data = window()
    data["pressure"] = [0, 0, 4, 1, 1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2]
    data["purge_valve_cmd"] = [31.44]*16
    evidence = measured_evidence(data)
    assert evidence["channels"]["pressure"] == [9, 2, 7, 2, 4]
    assert evidence["valve_gaps"]["purge"] == [-21.44, 21.44]
    assert evidence["channels"]["level"] == [0, 0, 0, 0, 16]
    assert "label" not in json.dumps(evidence)


@pytest.mark.parametrize("field,value", [
    ("pressure", [1]*15), ("pressure", [None]*16), ("pressure", [float("nan")]*16),
    ("pressure", [float("inf")]*16), ("pressure", [True]*16),
    ("scrammed", [0]*16), ("window_s", 0), ("window_s", True),
])
def test_invalid_or_misaligned_histories_fail(field, value):
    data = window()
    data[field] = value
    with pytest.raises(ValueError):
        measured_evidence(data)


def test_nonfinite_derived_arithmetic_fails():
    data = window()
    data["pressure"][:2] = [-1e308, 1e308]
    with pytest.raises(ValueError, match="derived"):
        measured_evidence(data)


@pytest.mark.parametrize("extra", ["truth_eval_only", "label", "recording_id", "measured_evidence"])
def test_hidden_truth_and_metadata_cannot_enter_evidence(extra):
    data = window()
    data[extra] = "secret"
    with pytest.raises(ValueError):
        measured_evidence(data)


def test_representation_preserves_every_wire_sample_and_does_not_mutate_input():
    data = window()
    before = copy.deepcopy(data)
    messages = evidence_messages("telemetry", json.dumps(data))
    payload = json.loads(messages[1]["content"])
    assert payload["data"] == before == data
    assert payload["measured_evidence"] == measured_evidence(data)
    assert "request_id" not in payload


def test_frozen_duration_uses_sample_intervals_instead_of_sample_count():
    data = window()
    row = {"input": data, "target": {"reason": "Pressure reading is stuck at 10.0 for 16 s while the process keeps moving."},
           "provenance": {"exact_recording_per_example": "not retained"}}
    issues = review_issues(row, measured_evidence(data))
    assert "claimed_frozen_duration_exceeds_visible_suffix" in issues
    assert "causal_claim_requires_review" in issues
    assert "exact_recording_not_retained" in issues
    row["target"]["reason"] = "Pressure reading is stuck at 10.0 for 15 s."
    assert "claimed_frozen_duration_exceeds_visible_suffix" not in review_issues(row, measured_evidence(data))


def test_review_flags_do_not_treat_steady_normal_readings_as_proven_faults():
    data = window()
    row = {"input": data, "target": {"label": "normal", "reason": "Readings are consistent with normal operation."},
           "provenance": {}}
    assert review_issues(row, measured_evidence(data)) == []
    assert row["target"]["label"] == "normal"


def test_token_budget_counts_ids_and_never_batch_encoding_keys():
    class Tokenizer:
        def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
            assert not tokenize
            return "rendered chat"

        def encode(self, text, *, add_special_tokens):
            assert text == "rendered chat" and not add_special_tokens
            return list(range(2200))

    assert token_count(Tokenizer(), []) == 2200


def test_source_matching_allows_one_sensor_transformation_but_retains_other_channels():
    original = window()
    transformed = copy.deepcopy(original)
    transformed["pressure"] = list(range(16))
    transformed["f1_valve_cmd"] = [50]*16
    assert set(fingerprints(original)) & set(fingerprints(transformed))
    transformed["level"] = list(range(16))
    assert not set(fingerprints(original)) & set(fingerprints(transformed))


def test_source_index_skips_unassigned_recordings_and_discards_hidden_truth(tmp_path, monkeypatch):
    import training.recover_slm_provenance as recovery

    directory = tmp_path / "training"
    directory.mkdir()
    data = window()
    measured = {k: data[k][0] for k in (*NUMERIC_FIELDS, "scrammed") if not k.endswith("_valve_cmd")}
    # An excluded recording deliberately lacks measured fields. Indexing it
    # would fail, so this verifies the allowlist is applied before construction.
    records = [{"rollout": "heldout", "truth_eval_only": {"secret": "never indexed"}}]
    records += [{"rollout": "allowed", "t": i*.5, "measured": measured,
                 "truth_eval_only": {"secret": "never indexed"}} for i in range(31)]
    (directory / "states.jsonl").write_text("".join(json.dumps(r)+"\n" for r in records))
    (directory / "observer_states.jsonl").write_text("")
    monkeypatch.setattr(recovery, "ROOT", tmp_path)
    index, candidates = source_index({"plant/allowed"})
    assert len(candidates) == 1
    assert candidates[0]["group"] == "plant/allowed"
    assert candidates[0]["commands_independently_recorded"] is False
    assert "secret" not in json.dumps(candidates)
    assert all(index[key] == {0} for key in fingerprints(data))
