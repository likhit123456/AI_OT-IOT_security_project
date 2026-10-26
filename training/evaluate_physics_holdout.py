"""Freeze candidate, score sealed holdout once, and record a release decision.

Release criteria are declared before opening holdout examples. This does not
claim hardware validity; collection includes explicitly marked synthetic faults.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from monitor.telemetry_classifier import TelemetryClassifier
from training.train_physics_telemetry import DEST, evaluate

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts/accuracy-v2"
CRITERIA = {
    "accuracy_min": 0.90,
    "false_alarm_rate_max": 0.05,
    "recall_min": {
        "normal": 0.95,
        "excursion": 0.9,
        "sensor_fault": 0.8,
        "actuator_fault": 0.8,
    },
}


def main(
    holdout=ART / "telemetry-holdout.jsonl",
    freeze_name="physics-frozen-candidate.json",
    output=DEST / "holdout-evaluation.json",
):
    validation = json.loads((DEST / "validation.json").read_text())
    model = DEST / "model.json"
    model_digest = hashlib.sha256(model.read_bytes()).hexdigest()
    assert model_digest == validation["model_sha256"]
    freeze = {
        "model_sha256": model_digest,
        "features_sha256": hashlib.sha256(
            (ROOT / "monitor/physics_features.py").read_bytes()
        ).hexdigest(),
        "validation_sha256": hashlib.sha256(
            (DEST / "validation.json").read_bytes()
        ).hexdigest(),
        "criteria": CRITERIA,
        "selection_used_holdout": False,
    }
    frozen = ART / freeze_name
    if frozen.exists():
        assert json.loads(frozen.read_text()) == freeze, (
            "Candidate changed after freeze; do not silently reuse this holdout."
        )
    else:
        frozen.write_text(json.dumps(freeze, indent=2), encoding="utf-8")
    path = holdout
    manifest = json.loads(path.with_suffix(".manifest.json").read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    runtime = TelemetryClassifier(model)
    predictions = [runtime.predict(row["window"]) for row in rows]
    score = evaluate([r["label"] for r in rows], [p["label"] for p in predictions])
    passed = (
        bool(validation["validation_gate_passed"])
        and score["accuracy"] >= CRITERIA["accuracy_min"]
        and score["false_alarm_rate"] <= CRITERIA["false_alarm_rate_max"]
        and all(
            score["per_class_recall"][k] >= v for k, v in CRITERIA["recall_min"].items()
        )
    )
    report = {
        "freeze": freeze,
        "holdout_sha256": manifest["sha256"],
        "score": score,
        "passed_release_gate": passed,
        "by_kind": {},
        "predictions": [
            {
                "id": r["id"],
                "source": r["source"],
                "kind": r["kind"],
                "gold": r["label"],
                **p,
            }
            for r, p in zip(rows, predictions)
        ],
    }
    for kind in sorted({r["kind"] for r in rows}):
        subset = [(r, p) for r, p in zip(rows, predictions) if r["kind"] == kind]
        report["by_kind"][kind] = {
            "n": len(subset),
            "correct": sum(r["label"] == p["label"] for r, p in subset),
        }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("predictions", "by_kind")},
            indent=2,
        )
    )
    print(
        "Confusion:",
        dict(Counter((r["label"], p["label"]) for r, p in zip(rows, predictions))),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=ART / "telemetry-holdout.jsonl")
    parser.add_argument("--freeze-name", default="physics-frozen-candidate.json")
    parser.add_argument("--output", type=Path, default=DEST / "holdout-evaluation.json")
    args = parser.parse_args()
    main(args.holdout, args.freeze_name, args.output)
