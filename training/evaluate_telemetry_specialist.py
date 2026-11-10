"""Validate conservative correction policy, then replay it on historical LLM outputs.

Validation determines activation. The historical test is a development comparison,
not a fresh blind test or a new inference run of the LLM weights.
"""
import hashlib
import json
import math
import time

from l2_monitor.telemetry_classifier import TelemetryClassifier
from training.build_clean_observer_dataset import OUT
from training.train_telemetry_classifier import DEST, HISTORICAL, ROOT, load, metrics


def below_sensor_noise_floor(window: dict, reason: str) -> bool:
    """Reject small drift/noise claims, never dropouts, freezes or physical excursions.

    The fixed 0.5% window effect-size floor is validated on labeled held-out
    sensor examples before activation. This is not a safety permissive.
    """
    text = reason.lower()
    if any(word in text for word in ("frozen", "stuck", "drop", "zero", "constant", "flat")):
        return False
    if not any(word in text for word in ("drift", "nois", "jump")):
        return False
    channels = {"pressure": ("pressure",), "level": ("level",),
                "f1_flow": ("feed 1", "feed1", "f1_flow"), "f2_flow": ("feed 2", "feed2", "f2_flow"),
                "purge_flow": ("purge flow", "purge_flow"), "product_flow": ("product flow", "product_flow"),
                "a_in_purge": ("a-in-purge", "analyzer", "composition")}
    matches = [(text.find(name), key) for key, names in channels.items() for name in names if name in text]
    if not matches:
        return False
    channel = min(matches)[1]
    xs = window.get(channel, [])
    if len(xs) < 4 or any(x is None or not math.isfinite(x) or x <= 0 for x in xs):
        return False
    # A high pressure or out-of-band level can never be dismissed by this check.
    if max(window.get("pressure", [float("inf")])) >= 2850 or not all(30 <= x <= 70 for x in window.get("level", [])):
        return False
    return max(xs) - min(xs) < .005 * (sum(xs) / len(xs))


def main():
    model = TelemetryClassifier()
    threshold = .7
    validation = load(OUT / "observer_v3_val.jsonl")
    accepted = []
    for window, gold, _ in validation:
        correction = model.correct(window, {"label": "normal"}, threshold)
        if correction["label"] != "normal":
            accepted.append((gold, correction["label"]))
    errors = sum(gold != predicted for gold, predicted in accepted)
    noise_rejections = []
    for line in (OUT / "observer_v3_val.jsonl").read_text(encoding="utf-8").splitlines():
        messages = json.loads(line)["messages"]
        gold = json.loads(messages[2]["content"])
        if gold["label"] == "sensor_fault":
            noise_rejections.append(below_sensor_noise_floor(json.loads(messages[1]["content"]), gold["reason"]))
    noise_errors = sum(noise_rejections)
    noise_passes = bool(noise_rejections) and noise_errors == 0
    enabled = len(accepted) >= 20 and errors == 0
    policy = {"enabled": enabled, "confidence_threshold": threshold, "validation_corrections": len(accepted),
              "sensor_noise_calibration": False, "noise_candidate_passed": noise_passes,
              "noise_validation_sensor_examples": len(noise_rejections), "noise_validation_missed_faults": noise_errors,
              "validation_correction_errors": errors, "validation_false_alarms": sum(gold == "normal" for gold, _ in accepted),
              "model_sha256": hashlib.sha256((DEST / "model.json").read_bytes()).hexdigest(),
              "scope": "telemetry only; correct normal LLM verdicts; never feeds authority"}
    baseline = json.loads((ROOT / "model/scram-observer-v3-gguf/eval_observer_test_unseen.json").read_text())
    rows = load(HISTORICAL)
    assert len(rows) == len(baseline["predictions"])
    assert all(gold == old["gold"] for (_, gold, _), old in zip(rows, baseline["predictions"]))
    changes = []
    predictions = {}
    start = time.perf_counter()
    for index, ((window, gold, key), old) in enumerate(zip(rows, baseline["predictions"])):
        updated = model.correct(window, {"label": old["pred"]}, threshold)
        predictions[key] = updated["label"]
        if updated["label"] != old["pred"]:
            changes.append({"index": index, "gold": gold, "before": old["pred"], "after": updated["label"]})
    replay_ms = 1000 * (time.perf_counter() - start) / len(rows)
    corrected_metrics = metrics(rows, lambda window: predictions[json.dumps(window, sort_keys=True, separators=(",", ":"))])
    corrected_metrics.pop("mean_inference_ms")
    report = {"activation_policy": policy,
              "correction_ms_per_example": replay_ms,
              "benchmark_sha256": hashlib.sha256(HISTORICAL.read_bytes()).hexdigest(),
              "comparison": "Historical 480-example benchmark replay; development evidence only; no LLM weight changes.",
              "baseline": {k: baseline[k] for k in ("accuracy", "per_class_recall", "false_alarm_rate", "detection_rate")},
              "corrected": corrected_metrics,
              "changes": changes}
    (DEST / "policy.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")
    (DEST / "hybrid-evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
