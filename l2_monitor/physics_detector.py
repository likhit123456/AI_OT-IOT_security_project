"""Release-gated measured-physics classifier, restricted to advisory telemetry."""

import hashlib
import json
from pathlib import Path

from l2_monitor.telemetry_classifier import TelemetryClassifier

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "model/telemetry-physics"


class PhysicsDetector(TelemetryClassifier):
    def correct(self, window, verdict, threshold=None):
        candidate = self.predict(window)
        return {
            **candidate,
            "source": "llm+physics-detector",
            "llm_label": verdict.get("label"),
        }


def approved_physics_detector():
    policy_path = DEST / "policy.json"
    if not policy_path.exists():
        return None
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if not policy.get("enabled"):
        return None
    report_path = DEST / "final-holdout-evaluation.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("passed_release_gate"):
        raise ValueError("physics detector failed release gate")
    targets = {
        "model_sha256": DEST / "model.json",
        "features_sha256": ROOT / "l2_monitor/physics_features.py",
        "validation_sha256": DEST / "validation.json",
        "report_sha256": report_path,
    }
    for key, path in targets.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != policy.get(key):
            raise ValueError(f"physics detector changed since validation: {key}")
        if key != "report_sha256" and digest != report["freeze"][key]:
            raise ValueError("physics detector release evidence mismatch")
    return PhysicsDetector(DEST / "model.json")
