"""Audit only saved train/validation supervision before a new SLM experiment.

Never opens the old final test, creates training targets, relabels data or trains.
Outputs a review queue and token budget for a measured-only representation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from monitor.telemetry_evidence import (
    VERSION,
    evidence_messages,
    measured_evidence,
)
from training.shared_slm_data import ROOT, canonical, sha

DATA = ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261102-v1/data-v1"
RUN = ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2"
NAMES = {"Pressure": "pressure", "Level": "level", "Feed 1 flow": "f1_flow", "Feed 2 flow": "f2_flow",
         "Purge flow": "purge_flow", "Product flow": "product_flow", "A-in-purge analyzer": "a_in_purge"}


def token_count(tokenizer, messages, generation_prompt=False):
    # Transformers versions differ in apply_chat_template's tokenized return
    # type. Render then encode explicitly: len(BatchEncoding) counts keys.
    rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=generation_prompt)
    return len(tokenizer.encode(rendered, add_special_tokens=False))


def review_issues(row, evidence):
    """Review flags, never authoritative corrections of historical class labels."""
    reason = row["target"]["reason"]
    issues = []
    # Construction templates make assertions not established by their arithmetic.
    for phrase in ("no matching change", "wider than the process supports", "while the process keeps moving"):
        if phrase in reason:
            issues.append("causal_claim_requires_review")
            break
    if row["provenance"].get("exact_recording_per_example", "").startswith("not retained"):
        issues.append("exact_recording_not_retained")
    if "valves follow their commands" in reason and any(v[1] > 0.1 for v in evidence["valve_gaps"].values()):
        issues.append("claims_following_despite_observed_gap")
    match = re.search(r"gap up to ([0-9.]+)%", reason)
    if match:
        valve = reason.split()[0].lower()
        if valve in evidence["valve_gaps"] and abs(float(match[1])-evidence["valve_gaps"][valve][1]) > 0.061:
            issues.append("target_gap_number_mismatch")
    for name, field in NAMES.items():
        if not reason.startswith(name):
            continue
        metrics = evidence["channels"][field]
        match = re.search(r"(?:spans |by )([0-9.]+)", reason)
        if match:
            expected = metrics[0] if "spans " in reason else abs(metrics[1])
            tolerance = 0.0021 if field == "a_in_purge" else 0.061
            if abs(float(match[1])-expected) > tolerance:
                issues.append("target_range_or_delta_number_mismatch")
        match = re.search(r"for ([0-9.]+) s", reason)
        if match:
            # The visible constant suffix spans (N-1)*dt, not N*dt.
            xs = row["input"][field]
            suffix = 1
            for value in reversed(xs[:-1]):
                if value != xs[-1]:
                    break
                suffix += 1
            visible_s = (suffix-1)*row["input"]["window_s"]/(len(xs)-1)
            if float(match[1]) > visible_s + 0.51:
                issues.append("claimed_frozen_duration_exceeds_visible_suffix")
    return issues


def audit(data, destination, tokenizer=None):
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    paths = {split: data / f"{split}.jsonl" for split in ("train", "val")}
    for split, path in paths.items():
        if sha(path) != manifest["splits"][split]["sha256"]:
            raise ValueError(f"{split} source hash mismatch")
    if set(manifest["splits"]["train"]["recording_ids"]) & set(manifest["splits"]["val"]["recording_ids"]):
        raise ValueError("overlapping source recording groups")
    destination.mkdir(parents=True, exist_ok=False)
    report = {"representation": VERSION, "status": "review_only_not_training_ready",
              "sealed_or_final_test_read": False, "labels_changed": False,
              "source_manifest_sha256": sha(data / "manifest.json"),
              "source_hashes": {split: sha(path) for split, path in paths.items()},
              "code_hashes": {"audit": sha(Path(__file__)),
                              "evidence": sha(ROOT / "monitor/telemetry_evidence.py")},
              "splits": {}, "token_audit": {"performed": tokenizer is not None, "context": 3072,
                  "reserved_generated_tokens": 192, "truncation_allowed": False},
              "limitations": ["Historical telemetry labels remain scenario/augmentation labels, not human gold.",
                              "Equal samples, zeros and command gaps alone do not prove a fault.",
                              "Copied actual positions are not independent evidence of controller commands.",
                              "Old train/validation recordings cannot be an independent new final holdout.",
                              "No supervised training dataset is emitted by this audit."]}
    seen = set()
    with (destination / "review-queue.jsonl").open("w", encoding="utf-8", newline="\n") as queue:
        for split, path in paths.items():
            counts, flags, tokens = Counter(), Counter(), []
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                digest = hashlib.sha256(canonical(row["input"]).encode()).hexdigest()
                if digest != row["input_sha256"] or digest in seen:
                    raise ValueError("input identity mismatch or duplicate within/across splits")
                seen.add(digest)
                messages = evidence_messages(row["kind"], json.dumps(row["input"]))
                # Ensure all raw histories survived the representation change.
                if json.loads(messages[1]["content"])["data"] != row["input"]:
                    raise ValueError("representation changed original input")
                counts[f"{row['kind']}/{row['target']['label']}"] += 1
                if tokenizer is not None:
                    prompt_n = token_count(tokenizer, messages, generation_prompt=True)
                    total_n = token_count(tokenizer, messages + [row["messages"][-1]])
                    tokens.append({"input_sha256": digest, "prompt": prompt_n, "total": total_n})
                if row["kind"] != "telemetry":
                    continue
                evidence = measured_evidence(row["input"])
                issues = review_issues(row, evidence)
                flags.update(issues)
                queue.write(json.dumps({"split": split, "input_sha256": digest, "input": row["input"],
                    "historical_target": row["target"], "provenance": row["provenance"],
                    "measured_evidence": evidence, "review_flags": issues, "human_review": "pending"}, allow_nan=False)+"\n")
            report["splits"][split] = {"counts": dict(counts), "review_flag_counts": dict(flags),
                "recording_groups": manifest["splits"][split]["recording_ids"]}
            if tokens:
                report["splits"][split]["tokens"] = {"max_prompt": max(t["prompt"] for t in tokens),
                    "max_total_with_original_target": max(t["total"] for t in tokens),
                    "over_context_with_generation_reserve": sum(t["prompt"]+192 > 3072 for t in tokens),
                    "largest_prompt": max(tokens, key=lambda t: t["prompt"])}
    report["review_queue_sha256"] = sha(destination / "review-queue.jsonl")
    if tokenizer is not None:
        report["token_audit"]["tokenizer_sha256"] = sha(ROOT / "artifacts/optimization/candidates/base-qwen2.5-0.5b/tokenizer.json")
    (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--destination", type=Path, default=RUN / "supervision-audit")
    parser.add_argument("--tokens", action="store_true", help="Use the pinned local tokenizer, offline")
    args = parser.parse_args()
    tokenizer = None
    if args.tokens:
        from training.shared_slm_native import BASE, tokenizer_for
        tokenizer = tokenizer_for(BASE)
    print(json.dumps(audit(args.data, args.destination, tokenizer), indent=2))


if __name__ == "__main__":
    main()
