import json

import pytest

from training import evaluate_combined_development as evaluation
from training.shared_slm_data import sha


def test_replay_counts_unconfirmed_as_neither_correct_nor_normal(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs.jsonl"
    rows = [{"kind": "telemetry", "input": {"id": i}, "target": {"label": "normal"},
             "input_sha256": str(i), "provenance": {"source_recordings": [f"recording-{i}"]}}
            for i in range(2)]
    inputs.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    predictions = tmp_path / "predictions.json"
    predictions.write_text(json.dumps({"split": "validation", "identity": {"input_sha256": sha(inputs)},
        "rows": [{"task": "telemetry", "gold": "normal", "candidate": "normal", "input_sha256": str(i)}
                 for i in range(2)]}), encoding="utf-8")
    class Detector:
        def predict(self, window):
            return {"label": "normal", "confidence": 1}
    monkeypatch.setattr(evaluation, "approved_specialist", Detector)
    monkeypatch.setattr(evaluation, "telemetry_review", lambda window, raw, cpu, aligned:
        {"label": "normal" if window["id"] == 0 else None,
         "status": "normal" if window["id"] == 0 else "unconfirmed"})
    output = tmp_path / "result.json"
    report = evaluation.evaluate(inputs, predictions, output)
    assert report["per_class"]["telemetry/normal"]["correct"] == 1
    assert report["per_class"]["telemetry/normal"]["unconfirmed_or_insufficient"] == 1
    assert report["per_class"]["telemetry/normal"]["recording_groups"] == ["recording-0", "recording-1"]
    with pytest.raises(FileExistsError):
        evaluation.evaluate(inputs, predictions, output)


def test_replay_rejects_wrong_input_identity_before_loading_detector(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs.jsonl"
    inputs.write_text("{}\n", encoding="utf-8")
    predictions = tmp_path / "predictions.json"
    predictions.write_text(json.dumps({"split": "validation", "identity": {"input_sha256": "wrong"}}))
    monkeypatch.setattr(evaluation, "approved_specialist", lambda: pytest.fail("should not load detector"))
    with pytest.raises(ValueError, match="exactly these inputs"):
        evaluation.evaluate(inputs, predictions, tmp_path / "result.json")
