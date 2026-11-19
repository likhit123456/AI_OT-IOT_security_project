"""Replay saved DEVELOPMENT predictions through the current combined review.

This is a diagnostic, not a fresh model evaluation or an independent test. It
never opens the previously exposed sealed recordings or sends plant commands.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from monitor.evidence_review import proposal_review, telemetry_review
from monitor.telemetry_classifier import approved_specialist
from training.shared_slm_data import sha
from training.train_demo_observer import DESTINATION


def evaluate(inputs: Path, predictions: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    rows = [json.loads(line) for line in inputs.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = json.loads(predictions.read_text(encoding="utf-8"))
    if report.get("split") != "validation" or report.get("identity", {}).get("input_sha256") != sha(inputs):
        raise ValueError("Predictions must be a validation report for exactly these inputs")
    index = {row["input_sha256"]: row for row in rows}
    predictions_rows = report["rows"]
    if (len(index) != len(rows) or len(predictions_rows) != len(rows)
            or len({row["input_sha256"] for row in predictions_rows}) != len(rows)
            or {row["input_sha256"] for row in predictions_rows} != set(index)):
        raise ValueError("Inputs and predictions must match one-to-one")
    detector = approved_specialist()
    if detector is None:
        raise RuntimeError("Approved CPU detector is unavailable; cannot replay the combined system")
    results = []
    for prediction in predictions_rows:
        source = index[prediction["input_sha256"]]
        task, gold, raw = prediction["task"], prediction["gold"], prediction["candidate"]
        if source["kind"] != task or source["target"]["label"] != gold:
            raise ValueError("Prediction task or gold label disagrees with source")
        if task == "telemetry":
            cpu = detector.predict(source["input"])
            review = telemetry_review(source["input"], raw, cpu, aligned=True)
        elif task == "proposals":
            cpu = None
            review = proposal_review(source["input"], raw)
        else:
            raise ValueError(f"Unknown task: {task}")
        results.append({"input_sha256": prediction["input_sha256"], "task": task, "gold": gold,
                        "raw_model": raw, "cpu": cpu, "review": review,
                        "groups": source["provenance"]["source_recordings"],
                        "saved_model_latency_s": prediction.get("latency_s")})
    groups = defaultdict(list)
    for row in results:
        groups[(row["task"], row["gold"])].append(row)
    metrics = {}
    for (task, gold), items in sorted(groups.items()):
        labels = Counter(str(item["review"]["label"]) for item in items)
        statuses = Counter(item["review"]["status"] for item in items)
        correct = sum(item["review"]["label"] == gold for item in items)
        metrics[f"{task}/{gold}"] = {"n": len(items), "correct": correct, "fraction_correct": correct / len(items),
            "unconfirmed_or_insufficient": sum(item["review"]["label"] is None for item in items),
            "outputs": dict(labels), "statuses": dict(statuses),
            "recording_groups": sorted({group for item in items for group in item["groups"]})}
    result = {"purpose": "development diagnostic; NOT an independent accuracy estimate",
              "input_sha256": sha(inputs), "saved_predictions_sha256": sha(predictions),
              "weights_sha256": report.get("weights_sha256"),
              "limitations": ["Development windows were used to select this model.",
                  "Saved model predictions are replayed; no new inference was run.",
                  "All model and CPU windows are assumed aligned; asynchronous live timing is not tested.",
                  "Cases from the same recording are correlated; construction labels are not human gold.",
                  "Unconfirmed outcomes are not counted as correct or as normal."],
              "per_class": metrics, "rows": results}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=DESTINATION / "validation.jsonl")
    parser.add_argument("--predictions", type=Path, default=DESTINATION / "f16-development-validation.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.inputs, args.predictions, args.output)
    print(json.dumps({"purpose": result["purpose"], "per_class": result["per_class"]}, indent=2))


if __name__ == "__main__":
    main()
