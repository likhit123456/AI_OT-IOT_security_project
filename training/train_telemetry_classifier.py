"""Train a CPU telemetry specialist and compare against unchanged recorded LLM results.

No test-set fitting or hyperparameter search. Exports plain JSON for Python-only inference.
Run: python -m training.train_telemetry_classifier
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier

from monitor.telemetry_classifier import FEATURE_VERSION, TelemetryClassifier, features
from training.build_clean_observer_dataset import OUT, canonical

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "model/telemetry-classifier"
HISTORICAL = ROOT / "training/observer_v3_unseen.jsonl"


def load(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        turns = json.loads(line)["messages"]
        window, gold = json.loads(turns[1]["content"]), json.loads(turns[2]["content"])
        if window["input"] == "telemetry":
            rows.append((window, gold["label"], canonical(turns[1]["content"])))
    return rows


def metrics(rows, predict):
    confusion = {label: Counter() for label in ("normal", "excursion", "sensor_fault", "actuator_fault")}
    start = time.perf_counter()
    for window, gold, _ in rows:
        confusion[gold][predict(window)] += 1
    recalls = {label: confusion[label][label] / sum(confusion[label].values()) if confusion[label] else None
               for label in confusion}
    normals = sum(confusion["normal"].values())
    attacks = len(rows) - normals
    return {"n": len(rows), "accuracy": sum(confusion[k][k] for k in confusion) / len(rows),
            "per_class_recall": recalls,
            "false_alarm_rate": 1 - recalls["normal"] if normals else None,
            "detection_rate": 1 - sum(confusion[k]["normal"] for k in confusion if k != "normal") / attacks if attacks else None,
            "mean_inference_ms": 1000 * (time.perf_counter() - start) / len(rows),
            "confusion": confusion}


def main():
    splits = {s: load(OUT / f"observer_v3_{s}.jsonl") for s in ("train", "val", "unseen")}
    historical = load(HISTORICAL)
    # Defense in depth: reserve exact benchmark turns even when rebuilding future source pools.
    excluded = {row[2] for row in historical + splits["val"] + splits["unseen"]}
    train = [row for row in splits["train"] if row[2] not in excluded]
    if len(train) < 100:
        raise ValueError("not enough independent training telemetry")
    xs = np.array([features(row[0]) for row in train], dtype=np.float32)
    labels = [row[1] for row in train]
    model = ExtraTreesClassifier(n_estimators=64, max_depth=14, min_samples_leaf=2,
                                 class_weight="balanced", random_state=19, n_jobs=2)
    model.fit(xs, labels)
    trees = []
    for estimator in model.estimators_:
        tree = estimator.tree_
        probabilities = tree.value[:, 0, :]
        probabilities = probabilities / probabilities.sum(axis=1, keepdims=True)
        trees.append({"left": tree.children_left.tolist(), "right": tree.children_right.tolist(),
                      "feature": tree.feature.tolist(), "threshold": tree.threshold.tolist(),
                      "probabilities": probabilities.tolist()})
    DEST.mkdir(parents=True, exist_ok=True)
    output = {"feature_version": FEATURE_VERSION, "n_features": xs.shape[1], "classes": model.classes_.tolist(),
              "trees": trees, "training_manifest_sha256": hashlib.sha256((OUT / "manifest.json").read_bytes()).hexdigest()}
    path = DEST / "model.json"
    path.write_text(json.dumps(output, separators=(",", ":")), encoding="utf-8")
    runtime = TelemetryClassifier(path)
    # Verify that the dependency-free runtime matches the training library before evaluating it.
    assert [runtime.predict(row[0])["label"] for row in splits["val"]] == model.predict(
        np.array([features(row[0]) for row in splits["val"]], dtype=np.float32)).tolist()
    report = {"algorithm": "ExtraTrees", "seed": 19, "train_n": len(train), "model_bytes": path.stat().st_size,
              "extra_gpu_bytes": 0, "benchmark_fit_overlap": 0,
              "validation": metrics(splits["val"], lambda w: runtime.predict(w)["label"]),
              "clean_unseen": metrics(splits["unseen"], lambda w: runtime.predict(w)["label"]),
              "historical_unseen": metrics(historical, lambda w: runtime.predict(w)["label"])}
    baseline = json.loads((ROOT / "model/scram-observer-v3-gguf/eval_observer_test_unseen.json").read_text())
    report["historical_llm_baseline"] = {k: baseline[k] for k in ("n", "accuracy", "per_class_recall", "false_alarm_rate", "detection_rate")}
    report["comparison_note"] = "Same unchanged 480-example benchmark; historical LLM results, not a new LLM run. No model selection on this benchmark."
    (DEST / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
