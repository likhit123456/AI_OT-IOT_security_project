"""Paired non-actuating Ollama/physics replay on the frozen final holdout."""

import json
import urllib.request
from pathlib import Path

from monitor import observer_v3_format as V3
from monitor.physics_detector import DEST, PhysicsDetector
from monitor.telemetry_classifier import DEFAULT_MODEL, TelemetryClassifier
from training.train_physics_telemetry import evaluate


def main():
    rows = [
        json.loads(line)
        for line in Path("artifacts/accuracy-v2/telemetry-final-holdout.jsonl")
        .read_text()
        .splitlines()
    ]
    old, new = TelemetryClassifier(DEFAULT_MODEL), PhysicsDetector(DEST / "model.json")
    results = []
    for i, row in enumerate(rows):
        payload = {
            "model": "scram-observer-v3",
            "stream": False,
            "format": "json",
            "keep_alive": "30m",
            "messages": V3.messages(json.dumps(row["window"])),
            "options": {"temperature": 0, "num_ctx": 3072, "num_predict": 120},
        }
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = json.load(response)["message"]["content"]
        verdict, note = V3.parse(raw)
        baseline = (
            old.correct(row["window"], verdict) if verdict else {"label": "invalid"}
        )
        results.append(
            {
                "id": row["id"],
                "gold": row["label"],
                "raw": raw,
                "parse_note": note,
                "baseline": baseline,
                "physics": new.predict(row["window"]),
            }
        )
        Path("artifacts/accuracy-v2/paired-progress.json").write_text(
            json.dumps(results)
        )
        if (i + 1) % 20 == 0:
            print(f"{i + 1}/{len(rows)}", flush=True)
    report = {"model": "scram-observer-v3", "n": len(rows), "results": results}
    for key in ("baseline", "physics"):
        report[key] = evaluate(
            [r["gold"] for r in results], [r[key]["label"] for r in results]
        )
    Path("artifacts/accuracy-v2/paired-final.json").write_text(
        json.dumps(report, indent=2)
    )
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
