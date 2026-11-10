"""Deployment policy, data leakage, and telemetry-only routing checks."""
import hashlib
import json
from pathlib import Path

import pytest

from l2_monitor import observer_v3_format as V3
from l2_monitor.observer import Observer
from l2_monitor.telemetry_classifier import (
    TelemetryClassifier,
    approved_specialist,
    features,
)
from training.build_clean_observer_dataset import OUT, canonical

ROOT = Path(__file__).resolve().parents[1]


def test_clean_observer_split_has_no_prompt_or_recording_overlap():
    if not (OUT / "manifest.json").exists():
        pytest.skip("build the clean observer data first")
    manifest = json.loads((OUT / "manifest.json").read_text())
    seen_users, seen_recordings = set(), set()
    for split in ("train", "val", "unseen"):
        path = OUT / f"observer_v3_{split}.jsonl"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["splits"][split]["sha256"]
        users = [canonical(json.loads(line)["messages"][1]["content"]) for line in
                 (OUT / f"observer_v3_{split}.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(users) == len(set(users))
        assert not seen_users.intersection(users)
        seen_users.update(users)
        recordings = manifest["splits"][split]["recordings"]
        assert not seen_recordings.intersection(recordings)
        seen_recordings.update(recordings)


def test_specialist_never_suppresses_an_existing_alarm():
    model = TelemetryClassifier.__new__(TelemetryClassifier)
    model.predict = lambda _: pytest.fail("should not classify an existing alarm")
    verdict = {"label": "excursion", "score": .9, "reason": "r"}
    assert model.correct({}, verdict) is verdict


def test_specialist_uses_confident_faults_only():
    model = TelemetryClassifier.__new__(TelemetryClassifier)
    normal = {"label": "normal", "score": 0, "reason": "r"}
    for label, confidence in (("sensor_fault", .69), ("normal", .99), ("excursion", .99)):
        model.predict = lambda _, label=label, confidence=confidence: {"label": label, "confidence": confidence}
        assert model.correct({}, normal) is normal
    model.predict = lambda _: {"label": "sensor_fault", "confidence": .9, "score": .9, "reason": "r"}
    assert model.correct({}, normal)["source"] == "llm+telemetry-specialist"


def test_bad_windows_are_not_interpreted_as_normal():
    with pytest.raises(ValueError):
        features({"input": "telemetry", "pressure": [2700]})
    with pytest.raises(ValueError):
        features({"input": "proposals"})


def test_specialist_verdict_never_reaches_proposal_suspicion():
    class Specialist:
        def correct(self, *_):
            return {"source": "llm+telemetry-specialist", "label": "sensor_fault", "score": .9, "reason": "r"}
    obs = Observer(fmt=V3, background=False, telemetry_specialist=Specialist(),
                   http_post=lambda *_: {"message": {"content": '{"label":"normal","score":0,"reason":"r"}'}})
    obs._gate.acquire()
    obs._run(10, "telemetry", '{}')
    assert obs.last["telemetry"].source == "llm+telemetry-specialist"
    assert obs.last["telemetry"].llm_label == "normal"
    assert obs.suspicion() == 0


def test_shipped_specialist_matches_approved_model_digest():
    model = approved_specialist()
    assert model is not None
    policy_path = ROOT / "model/telemetry-physics/policy.json"
    policy = json.loads(policy_path.read_text())
    assert policy["enabled"] and policy["advisory_only"]
    assert hashlib.sha256((policy_path.parent / "model.json").read_bytes()).hexdigest() == policy["model_sha256"]
    assert model.data["feature_version"] == 2
