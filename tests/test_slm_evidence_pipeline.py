"""Supervision boundaries and opt-in training/serving parity for the new run."""
import json

import pytest

from monitor.observer_runtime import Request, UnifiedObserver, make_observer
from monitor.telemetry_evidence import NUMERIC_FIELDS, evidence_messages
from training import slm_evidence_data as data_builder
from training.freeze_evidence_candidate import build_final_data, quantization_failures
from training.slm_evidence_policy import validate_training_readiness


def samples():
    measured = {k: 10.0 for k in NUMERIC_FIELDS if not k.endswith("_valve_cmd")}
    measured.update(pressure=2700, level=44, a_in_purge=.47, scrammed=False)
    return [{"t": 45+i, "commands_valid": True, "fault_active": True,
             "commanded": {v: 10.0 for v in ("f1", "f2", "purge", "product")},
             "measured": dict(measured), "truth_eval_only": {"pressure": 2700, "liquid_level": 44,
                 "A_in_purge": .47, "f1_flow": 10, "f2_flow": 10, "purge_flow": 10, "product_flow": 10}}
            for i in range(16)]


def spec(label="normal", inputs=None):
    return {"recording_id": "train-native-00", "planned_class": label, "fault_inputs": inputs or {}}


def test_native_dropout_labels_use_hidden_truth_only_for_screening():
    rows = samples()
    for row in rows[-3:]:
        row["measured"]["pressure"] = 0
    row, issue = data_builder.telemetry_row(rows, spec("sensor_fault", {"tank_pressure_fault_mode": 4}), "source-hash")
    assert issue is None
    assert row["target"]["label"] == "sensor_fault"
    assert "3 zero samples" in row["target"]["reason"]
    payload = evidence_messages("telemetry", json.dumps(row["input"]))
    assert "truth_eval_only" not in str(payload)
    assert "source-hash" not in str(payload)
    assert row["provenance"]["human_gold"] is False


def test_invisible_native_sensor_fault_is_quarantined_instead_of_relabelled():
    row, issue = data_builder.telemetry_row(samples(), spec("sensor_fault", {"tank_pressure_fault_mode": 1}), "hash")
    assert row is None and issue == "native_sensor_fault_not_observable_or_confounded"


def test_missing_effective_commands_are_not_replaced_with_actual_positions():
    rows = samples()
    rows[-1].update(commands_valid=False, commanded=None)
    row, issue = data_builder.telemetry_row(rows, spec(), "hash")
    assert row is None and issue == "missing_effective_commands_or_interlock"


def test_actuator_targets_use_measured_command_gaps():
    rows = samples()
    for row in rows:
        row["commanded"]["product"] = 31.44
    row, issue = data_builder.telemetry_row(rows, spec("actuator_fault", {"product_stuck": 1}), "hash")
    assert issue is None
    assert "21.44 percentage points" in row["target"]["reason"]


def test_concurrent_excursion_is_quarantined_from_single_fault_supervision():
    rows = samples()
    rows[-1]["truth_eval_only"]["pressure"] = 2900
    row, issue = data_builder.telemetry_row(rows, spec(), "hash")
    assert row is None and issue == "concurrent_physical_excursion"


def test_preparation_never_opens_sealed_recordings_and_reports_support_shortfalls(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    specs = [{**spec(), "split": split, "recording_id": f"{split}-00"} for split in ("train", "val", "sealed")]
    (source / "collection-plan.json").write_text(json.dumps({"recordings": specs}))
    opened = []

    def read_recording(directory, current, plan_hash):
        assert current["split"] != "sealed"
        opened.append(current["split"])
        return samples(), {"sha256": "hash", "image_id": "image", "binary_sha256": "binary", "collector_sha256": "collector"}

    monkeypatch.setattr(data_builder, "read_recording", read_recording)
    result = data_builder.prepare(source, tmp_path / "prepared")
    assert opened == ["train", "val"]
    assert result["training_ready"] is False and result["readiness_errors"]
    assert result["sealed_data_opened"] is False


def test_readiness_rejects_old_dataset_before_any_training(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"wire_schema": "slm-v1"}))
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"representation": "measured-evidence-v2"}))
    with pytest.raises(ValueError, match="representation"):
        validate_training_readiness(tmp_path, policy)


def test_serving_evidence_schema_requires_external_request_identity():
    with pytest.raises(ValueError, match="identity"):
        UnifiedObserver(model="new-experiment", measured_evidence=True)


def test_evidence_schema_requires_explicit_model_alias(monkeypatch):
    monkeypatch.setenv("SCRAM_OBSERVER_SCHEMA", "measured-evidence-v2")
    monkeypatch.delenv("SCRAM_OBSERVER_MODEL", raising=False)
    with pytest.raises(ValueError, match="explicit model"):
        make_observer("unified")


def test_runtime_uses_identical_training_prompt_and_keeps_identity_external():
    row, issue = data_builder.telemetry_row(samples(), spec(), "hash")
    assert issue is None
    seen = []

    def post(url, body, timeout):
        seen.append(body)
        return {"message": {"content": '{"label":"normal","score":0,"reason":"measured"}'}}

    observer = UnifiedObserver(model="evidence-v2-test", include_identity=False, measured_evidence=True,
                               http_post=post, clock=lambda: 10)
    try:
        observer.paths["telemetry"].sequence = 1
        request = Request("telemetry", json.dumps(row["input"]), 0, 1, 0, 10, 20, observer.run_id)
        observer._execute(request)
        assert seen[0]["messages"] == evidence_messages("telemetry", json.dumps(row["input"]))
        assert request.identity not in json.dumps(seen[0])
        assert observer.last["telemetry"].label == "normal"
        assert observer.snapshot()["schema"] == "measured-evidence-v2"
    finally:
        observer.worker.close()


def test_higher_macro_f1_cannot_hide_a_proposal_class_regression():
    def quality(recall):
        return {"macro_f1": .9 if recall == 1 else .95,
                "proposals": {"per_class": {label: {"recall": recall if label == "normal" else 1}
                                            for label in ("normal", "setpoint_drift", "rationale_mismatch", "provenance_violation")},
                              "false_positives": 0, "unavailable": 0},
                "telemetry": {"unavailable": 0}}

    assert quantization_failures(quality(1), quality(.99)) == ["proposal_recall_decreased/normal"]


def test_new_holdout_is_not_opened_without_an_intact_new_freeze(tmp_path, monkeypatch):
    (tmp_path / "freeze.json").write_text(json.dumps({"wire_schema": "slm-v1"}))
    calls = []
    import training.freeze_evidence_candidate as freezer

    monkeypatch.setattr(freezer, "read_recording", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="frozen and intact"):
        build_final_data(tmp_path, tmp_path / "data", tmp_path / "recordings")
    assert calls == []


def test_old_final_test_builder_rejects_new_experiment_data(tmp_path):
    from training.slm_artifact_tools import freeze

    (tmp_path / "manifest.json").write_text(json.dumps({"wire_schema": "measured-evidence-v2"}))
    with pytest.raises(ValueError, match="historical final-test builder is prohibited"):
        freeze(tmp_path / "new-run", tmp_path, tmp_path / "model.gguf")
