"""Validation-only paired observer evaluation; no fitting or automatic promotion.

Use --sealed-test once after selecting a candidate to evaluate the existing test
split. Never use that output for tuning. Historical inputs/evidence are read only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

from monitor import observer_v2_format as V2
from monitor import observer_v3_format as V3
from monitor.observer import _http_post_json
from monitor.observer_runtime import LABELS, Request, UnifiedObserver
from monitor.telemetry_classifier import approved_specialist
from scripts.benchmark_observer_runtime import get_json, inventory, quantiles

ROOT = Path(__file__).resolve().parents[1]


def summarize(rows, field):
    if not any(field in row for row in rows):
        return {"n": len(rows), "status": "not_run", "reason": "unavailable backend, CPU-only run, or inapplicable task"}
    matrix = defaultdict(Counter)
    for row in rows:
        matrix[row["gold"]][row.get(field) or "unavailable"] += 1
    n = len(rows)
    normal = [r for r in rows if r["gold"] == "normal"]
    false_positives = sum(r.get(field) not in (None, "normal") for r in normal)
    faults = [r for r in rows if r["gold"] != "normal"]
    return {"n": n, "confusion": matrix,
            "per_class": {label: {"support": sum(counts.values()),
                                   "recall": counts[label]/sum(counts.values()),
                                   "precision": counts[label]/max(1, sum(m[label] for m in matrix.values()))}
                          for label, counts in matrix.items()},
            "false_positives": false_positives, "normal_denominator": len(normal),
            "false_positive_rate": false_positives/len(normal) if normal else None,
            "missed_faults": sum(r.get(field) in (None, "normal") for r in faults),
            "fault_classification_errors": sum(r.get(field) != r["gold"] for r in faults),
            "fault_denominator": len(faults),
            "unavailable": sum(r.get(field) is None for r in rows),
            "uncertainty_coverage": sum(r.get(field) is None for r in rows)/n if n else None}


def run(args):
    source = ROOT / "training/observer_clean" / ("observer_v3_unseen.jsonl" if args.sealed_test else "observer_v3_val.jsonl")
    pools = defaultdict(list)
    for line in source.read_text(encoding="utf-8").splitlines():
        messages = json.loads(line)["messages"]
        user = json.loads(messages[1]["content"])
        kind = user["input"]
        gold = json.loads(messages[2]["content"])["label"]
        if gold not in LABELS[kind]:
            raise ValueError("dataset role/label mismatch")
        pools[(kind, gold)].append((messages, gold))
    rng = random.Random(3407)
    selected = []
    for key, rows in sorted(pools.items()):
        rng.shuffle(rows)
        selected.extend((key[0], messages, gold) for messages, gold in rows[:args.per_class])
    report = {"schema_version": 1, "seed": 3407, "split": "test" if args.sealed_test else "validation",
              "dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "gates_sha256": hashlib.sha256((ROOT / "experiments/unified_observer_gates.json").read_bytes()).hexdigest(),
              "inventory": inventory(), "candidate_model": args.model,
              "cpu_only": args.cpu_only, "rows": [], "promotion": "blocked: runtime and quality gates require review"}
    available = "unavailable" not in report["inventory"]["installed"]
    report["real_inference_available"] = available
    detector = approved_specialist()
    observer = UnifiedObserver(model=args.model)
    for index, (kind, messages, gold) in enumerate(selected):
        user = json.loads(messages[1]["content"])
        row = {"task": kind, "gold": gold, "input_sha256": hashlib.sha256(messages[1]["content"].encode()).hexdigest()}
        if kind == "telemetry" and detector:
            tick = time.perf_counter()
            cpu = detector.predict(user)
            row.update(cpu=cpu["label"], cpu_latency_s=time.perf_counter()-tick)
        if available and not args.cpu_only:
            fmt = V2 if kind == "proposals" else V3
            model = "scram-observer" if kind == "proposals" else "scram-observer-v3"
            start = time.monotonic()
            try:
                body = _http_post_json("http://127.0.0.1:11434/api/chat", {
                    "model": model, "stream": False, "format": "json", "keep_alive": "30m",
                    "messages": fmt.messages(messages[1]["content"]),
                    "options": {"temperature": 0, "num_predict": 120,
                                "num_ctx": 2048 if kind == "proposals" else 3072}}, 20)
                parsed, note = fmt.parse(body.get("message", {}).get("content", ""), labels=LABELS[kind])
                row.update(specialist=parsed["label"] if parsed else None, specialist_raw=body, specialist_note=note)
                if kind == "telemetry" and detector and parsed:
                    row["deployed_combined"] = detector.correct(user, parsed)["label"]
            except (OSError, ValueError, TypeError) as exc:
                row.update(specialist=None, specialist_error=str(exc))
            row["specialist_latency_s"] = time.monotonic()-start
            observer.paths[kind].sequence = index+1
            queued = time.monotonic()
            observer._execute(Request(kind, messages[1]["content"], 0, index+1, 0, queued, queued+20))
            verdict = observer.last.get(kind)
            row.update(candidate=verdict.label if verdict else None, candidate_event=observer.log[-1])
            row["residency"] = get_json("http://127.0.0.1:11434/api/ps")
        report["rows"].append(row)
    report["metrics"] = {kind: {field: summarize([r for r in report["rows"] if r["task"] == kind], field)
                                for field in ("specialist", "candidate", "cpu", "deployed_combined")}
                         for kind in ("proposals", "telemetry")}
    report["cpu_latency_s"] = quantiles([r["cpu_latency_s"] for r in report["rows"] if "cpu_latency_s" in r])
    report["limitations"] = ["Static windows cannot measure detection delay, gradual-fault scheduling, or control jitter.",
                              "Paired evaluation loads comparison models; use unified-only replay for residency proof.",
                              "No calibrated model probabilities or statistical quality equivalence are claimed."]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="scram-observer")
    parser.add_argument("--per-class", type=int, default=30)
    parser.add_argument("--sealed-test", action="store_true")
    parser.add_argument("--cpu-only", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.per_class < 1:
        parser.error("per-class must be positive")
    run(args)
