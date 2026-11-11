"""Fresh paired LLM/specialist evaluation on balanced clean held-out telemetry.

Run: python -m training.benchmark_observer --per-class 12
Never trains or selects parameters. Stores inputs' hashes, raw model responses,
model IDs and dataset hashes so results can be reproduced and audited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from l2_monitor import observer_v3_format as F
from l2_monitor.telemetry_classifier import approved_specialist
from training.build_clean_observer_dataset import OUT


def summarize(rows, field):
    confusion = defaultdict(Counter)
    for row in rows:
        confusion[row["gold"]][row[field] or "invalid"] += 1
    normal = [r for r in rows if r["gold"] == "normal"]
    faults = [r for r in rows if r["gold"] != "normal"]
    return {"n": len(rows), "valid_output_rate": sum(r[field] is not None for r in rows) / len(rows),
            "accuracy": sum(r[field] == r["gold"] for r in rows) / len(rows),
            "per_class_recall": {label: counts[label] / sum(counts.values()) for label, counts in confusion.items()},
            "false_alarm_rate": sum(r[field] not in (None, "normal") for r in normal) / len(normal),
            "detection_rate": sum(r[field] not in (None, "normal") for r in faults) / len(faults),
            "confusion": confusion}


def run(per_class, destination):
    source = OUT / "observer_v3_unseen.jsonl"
    pools = defaultdict(list)
    for line in source.read_text(encoding="utf-8").splitlines():
        messages = json.loads(line)["messages"]
        pools[json.loads(messages[2]["content"])["label"]].append(messages)
    rng = random.Random(29)
    selected = []
    for label, examples in sorted(pools.items()):
        rng.shuffle(examples)
        selected.extend((label, messages) for messages in examples[:per_class])
    rng.shuffle(selected)
    specialist = approved_specialist()
    report = {"dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "seed": 29,
              "kind": "fresh paired inference; telemetry only; no fitting", "rows": []}
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
            report["installed_models"] = json.loads(response.read())["models"]
    except OSError:
        pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    for i, (gold, messages) in enumerate(selected):
        request = urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps({
            "model": "scram-observer-v3", "messages": messages[:2], "format": "json", "stream": False,
            "keep_alive": "30m", "options": {"temperature": 0, "num_ctx": 3072, "num_predict": 120}
        }).encode(), {"Content-Type": "application/json"})
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read())
            raw = body.get("message", {}).get("content", "")
            parsed, _ = F.parse(raw)
        except (OSError, ValueError) as exc:
            raw, parsed = str(exc), None
        corrected = specialist.correct(json.loads(messages[1]["content"]), parsed) if parsed and specialist else parsed
        report["rows"].append({"gold": gold, "baseline": parsed["label"] if parsed else None,
            "hybrid": corrected["label"] if corrected else None, "raw": raw,
            "input_sha256": hashlib.sha256(messages[1]["content"].encode()).hexdigest(),
            "latency_s": round(time.perf_counter() - start, 3)})
        report["baseline"] = summarize(report["rows"], "baseline") if i >= len(selected) - 1 else None
        report["hybrid"] = summarize(report["rows"], "hybrid") if i >= len(selected) - 1 else None
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if (i + 1) % 8 == 0 or i + 1 == len(selected):
            print(f"{i+1}/{len(selected)} fresh observer examples evaluated", flush=True)
    print(json.dumps({k: report[k] for k in ("baseline", "hybrid")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-class", type=int, default=12)
    parser.add_argument("--output", type=Path, default=Path("artifacts/fresh-observer-benchmark.json"))
    args = parser.parse_args()
    if args.per_class < 1:
        parser.error("per-class must be positive")
    run(args.per_class, args.output)
