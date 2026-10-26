"""Fit and select measured-physics telemetry models on TRAIN/VALIDATION only.

This program never opens the historical or clean unseen test examples except
to exclude their exact user hashes from fitting. Fresh collection is separate.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier

from monitor.physics_features import VERSION, physics_features
from monitor.telemetry_classifier import TelemetryClassifier
from training import build_observer_dataset as B
from training.build_clean_observer_dataset import OUT, canonical
from training.train_telemetry_classifier import HISTORICAL, load

DEST = Path("model/telemetry-physics")


def evaluate(gold, predicted):
    labels = ("normal", "excursion", "sensor_fault", "actuator_fault")
    recall = {
        label: sum(g == p == label for g, p in zip(gold, predicted))
        / sum(g == label for g in gold)
        for label in labels
    }
    return {
        "n": len(gold),
        "accuracy": sum(g == p for g, p in zip(gold, predicted)) / len(gold),
        "per_class_recall": recall,
        "balanced_accuracy": sum(recall.values()) / 4,
        "false_alarm_rate": 1 - recall["normal"],
    }


def main():
    train, validation = (load(OUT / f"observer_v3_{s}.jsonl") for s in ("train", "val"))
    reserved = {
        r[2]
        for r in validation + load(OUT / "observer_v3_unseen.jsonl") + load(HISTORICAL)
    }
    train = [r for r in train if r[2] not in reserved]
    manifest = json.loads((OUT / "manifest.json").read_text())
    train_sources = set(manifest["splits"]["train"]["recordings"])
    seen = {r[2] for r in train}
    augmented = 0
    for name, records in B.load_rollouts(B.OBS_STATES).items():
        if f"native/{name}" not in train_sources:
            continue
        pools, _ = B.recorded_telemetry({}, {name: records})
        for examples in pools["seen"].values():
            # Limit overlapping windows while ensuring actual capacity faults
            # are not overwhelmed by thousands of synthetic command offsets.
            for example in examples[:: max(1, B.REC // 2)][:30]:
                label, _, user, _ = example
                key = canonical(user)
                if key not in seen and key not in reserved:
                    train.append((json.loads(user), label, key))
                    seen.add(key)
                    augmented += 1
    x = np.array([physics_features(r[0]) for r in train], dtype=np.float32)
    y = [r[1] for r in train]
    observable = [
        label not in ("sensor_fault", "actuator_fault") or max(row[96:114]) > 1e-6
        for row, label in zip(x, y)
    ]
    x = x[observable]
    y = [label for label, keep in zip(y, observable) if keep]
    xv = np.array([physics_features(r[0]) for r in validation], dtype=np.float32)
    yv = [r[1] for r in validation]
    trials, models = [], []
    for family in ("extra", "random"):
        for leaf in (1, 3, 8):
            factory = (
                ExtraTreesClassifier if family == "extra" else RandomForestClassifier
            )
            model = factory(
                n_estimators=128,
                max_depth=18,
                min_samples_leaf=leaf,
                max_features=1.0,
                class_weight="balanced",
                random_state=419,
                n_jobs=2,
            )
            model.fit(x, y)
            probability = model.predict_proba(xv)
            for threshold in (0.0, 0.45, 0.55, 0.65):
                indexes = probability.argmax(axis=1)
                predicted = [
                    model.classes_[i] if max(p) >= threshold else "normal"
                    for p, i in zip(probability, indexes)
                ]
                result = evaluate(yv, predicted)
                result.update(family=family, leaf=leaf, confidence_threshold=threshold)
                trials.append(result)
                models.append(model)
    feasible = [
        i
        for i, r in enumerate(trials)
        if r["false_alarm_rate"] <= 0.05
        and min(
            r["per_class_recall"][label]
            for label in ("sensor_fault", "actuator_fault", "excursion")
        )
        >= 0.8
    ]
    pool = feasible or list(range(len(trials)))
    best = max(
        pool,
        key=lambda i: (
            trials[i]["balanced_accuracy"] - 2 * trials[i]["false_alarm_rate"]
        ),
    )
    model, selected = models[best], trials[best]
    trees = []
    for estimator in model.estimators_:
        t = estimator.tree_
        probability = t.value[:, 0, :]
        probability = probability / probability.sum(axis=1, keepdims=True)
        trees.append(
            {
                "left": t.children_left.tolist(),
                "right": t.children_right.tolist(),
                "feature": t.feature.tolist(),
                "threshold": t.threshold.tolist(),
                "probabilities": probability.tolist(),
            }
        )
    model_data = {
        "feature_version": VERSION,
        "n_features": x.shape[1],
        "classes": model.classes_.tolist(),
        "trees": trees,
        "normal_below_confidence": selected["confidence_threshold"],
    }
    DEST.mkdir(parents=True, exist_ok=True)
    file = DEST / "model.json"
    file.write_text(json.dumps(model_data, separators=(",", ":")), encoding="utf-8")
    # Dependency-free inference must match the fitted forest on every validation window.
    runtime = TelemetryClassifier(file)
    assert [runtime.predict(r[0])["label"] for r in validation] == model.predict(
        xv
    ).tolist()
    result = {
        "selection": "whole-recording validation only",
        "fit_n": len(train),
        "native_training_windows_added": augmented,
        "feature_count": x.shape[1],
        "validation_gate_passed": bool(feasible),
        "selected": selected,
        "trials": trials,
        "model_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        "model_bytes": file.stat().st_size,
    }
    (DEST / "validation.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in result.items() if k != "trials"}, indent=2))


if __name__ == "__main__":
    main()
