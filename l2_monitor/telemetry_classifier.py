"""Small advisory telemetry classifier; JSON trees, no sklearn runtime dependency.

Only measured fields and commanded positions are features. This classifier does
not control valves or contribute to governance authority.
"""
from __future__ import annotations

import hashlib
import json
import math
from itertools import pairwise
from pathlib import Path

FEATURE_VERSION = 1
DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "model/telemetry-classifier/model.json"
CHANNELS = {"pressure": 2700., "level": 45., "f1_flow": 200., "f2_flow": 40.,
            "purge_flow": 7., "product_flow": 100., "a_in_purge": .47,
            **{f"{v}_valve_{kind}": 100. for v in ("f1", "f2", "purge", "product") for kind in ("pos", "cmd")}}


def _stats(xs):
    n = len(xs)
    mean = sum(xs) / n
    delta = [b - a for a, b in pairwise(xs)]
    variance = sum((x - mean) ** 2 for x in xs) / n
    return [xs[0], xs[-1], mean, min(xs), max(xs), max(xs) - min(xs), math.sqrt(variance),
            xs[-1] - xs[0], max(map(abs, delta), default=0.),
            sum(abs(x) for x in delta) / max(1, len(delta)),
            sum(x == 0 for x in xs) / n, sum(abs(x) < 1e-7 for x in delta) / max(1, len(delta))]


def features(window: dict) -> list[float]:
    if window.get("input") != "telemetry":
        raise ValueError("telemetry input required")
    n = len(window.get("pressure", []))
    if n < 4 or n > 128:
        raise ValueError("telemetry window must contain 4-128 samples")
    values = []
    sensor_stats = []
    for channel, scale in CHANNELS.items():
        xs = window.get(channel)
        if not isinstance(xs, list) or len(xs) != n or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in xs
        ):
            raise ValueError(f"invalid channel {channel}")
        stats = _stats([x / scale for x in xs])
        values.extend(stats)
        if channel in ("pressure", "level", "f1_flow", "f2_flow", "purge_flow", "product_flow", "a_in_purge"):
            sensor_stats.append(stats)
    # Shared statistics allow detecting abnormal shapes on channels absent from training faults.
    for i in (5, 6, 7, 8, 9, 10, 11):
        values.extend(sorted(abs(s[i]) for s in sensor_stats))
    for valve, cv, scale in (("f1", 6.4046, 200.), ("f2", .4246, 40.),
                              ("purge", .352, 7.), ("product", .0417, 100.)):
        pos, cmd = window[f"{valve}_valve_pos"], window[f"{valve}_valve_cmd"]
        values.extend(_stats([(a - c) / 100 for a, c in zip(pos, cmd)]))
        expected = [cv * a * (math.sqrt(max(0, p - 100)) if valve in ("purge", "product") else 1)
                    for a, p in zip(pos, window["pressure"])]
        residual = [(q - e) / scale for q, e in zip(window[f"{valve}_flow"], expected)]
        values.extend(_stats(residual))
    return values


class TelemetryClassifier:
    def __init__(self, path: Path = DEFAULT_MODEL):
        self.data = json.loads(path.read_text(encoding="utf-8"))
        if self.data.get("feature_version") not in (FEATURE_VERSION, 2):
            raise ValueError("telemetry classifier feature version mismatch")

    def predict(self, window: dict) -> dict:
        if self.data["feature_version"] == 2:
            from l2_monitor.physics_features import physics_features
            xs = physics_features(window)
        else:
            xs = features(window)
        if len(xs) != self.data["n_features"]:
            raise ValueError("telemetry classifier input size mismatch")
        probabilities = [0.] * len(self.data["classes"])
        for tree in self.data["trees"]:
            node = 0
            while tree["left"][node] != -1:
                node = tree["left"][node] if xs[tree["feature"][node]] <= tree["threshold"][node] else tree["right"][node]
            for i, value in enumerate(tree["probabilities"][node]):
                probabilities[i] += value / len(self.data["trees"])
        index = max(range(len(probabilities)), key=probabilities.__getitem__)
        label = self.data["classes"][index]
        return {"label": label, "confidence": round(probabilities[index], 4),
                "score": round(1 - probabilities[self.data["classes"].index("normal")], 4),
                "reason": f"Measured telemetry classifier: {label.replace('_', ' ')}.",
                "source": "telemetry-classifier", "advisory_only": True}

    def correct(self, window: dict, verdict: dict, threshold: float = .7) -> dict:
        """Only add a confident fault finding to an otherwise normal LLM verdict.

        Existing alarms are preserved. This never changes proposal evidence or authority.
        """
        if verdict.get("label") != "normal":
            return verdict
        candidate = self.predict(window)
        if candidate["label"] in ("sensor_fault", "actuator_fault") and candidate["confidence"] >= threshold:
            return {**candidate, "source": "llm+telemetry-specialist", "llm_label": "normal"}
        return verdict


def approved_specialist():
    from l2_monitor.physics_detector import approved_physics_detector
    physics = approved_physics_detector()
    if physics is not None:
        return physics
    policy = DEFAULT_MODEL.parent / "policy.json"
    if not policy.exists():
        return None
    data = json.loads(policy.read_text(encoding="utf-8"))
    if not data.get("enabled"):
        return None
    if hashlib.sha256(DEFAULT_MODEL.read_bytes()).hexdigest() != data.get("model_sha256"):
        raise ValueError("telemetry specialist changed since validation")
    if data.get("sensor_noise_calibration"):
        raise ValueError("sensor-noise suppression is not approved for deployment")
    return TelemetryClassifier()

