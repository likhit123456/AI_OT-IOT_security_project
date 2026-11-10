"""Physics release integrity and advisory routing regression checks."""

import json

import pytest

from l2_monitor import observer_v3_format as V3
from l2_monitor.observer import Observer
from l2_monitor.physics_detector import DEST, PhysicsDetector, approved_physics_detector
from l2_monitor.physics_features import physics_features


def normal_window():
    # Fixed simulator test fixture; no live inference or plant writes.
    path = DEST.parents[1] / "artifacts/accuracy-v2/telemetry-final-holdout.jsonl"
    return next(
        json.loads(line)["window"]
        for line in path.read_text().splitlines()
        if json.loads(line)["label"] == "normal"
    )


def test_release_evidence_and_prediction():
    detector = approved_physics_detector()
    assert isinstance(detector, PhysicsDetector)
    window = normal_window()
    assert detector.predict(window)["label"] == "normal"
    before = physics_features(window)
    window.update(label="actuator_fault", rationale="attack", truth={"fault": True})
    assert physics_features(window) == before
    window["window_s"] = 0
    with pytest.raises(ValueError):
        detector.predict(window)


def test_changed_model_evidence_is_rejected(monkeypatch, tmp_path):
    from l2_monitor import physics_detector as module

    for path in DEST.iterdir():
        if path.is_file():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    monkeypatch.setattr(module, "DEST", tmp_path)
    (tmp_path / "model.json").write_text("{}")
    with pytest.raises(ValueError, match="changed since validation"):
        module.approved_physics_detector()


def test_correction_retains_llm_label_and_has_no_authority():
    detector = approved_physics_detector()
    window = normal_window()
    result = detector.correct(window, {"label": "sensor_fault"})
    assert result["label"] == "normal"
    assert result["llm_label"] == "sensor_fault"
    assert result["advisory_only"]
    observer = Observer(
        fmt=V3,
        background=False,
        telemetry_specialist=detector,
        http_post=lambda *_: {
            "message": {"content": '{"label":"sensor_fault","score":0.9,"reason":"r"}'}
        },
    )
    observer._gate.acquire()
    observer._run(10, "telemetry", json.dumps(window))
    assert observer.last["telemetry"].source == "llm+physics-detector"
    assert observer.last["telemetry"].llm_label == "sensor_fault"
    assert observer.suspicion() == 0
