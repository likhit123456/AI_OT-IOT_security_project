"""Short, separately versioned demo adaptation; never modifies the source run.

Uses existing train/validation groups only, with no final-test access. Small
validation is a development check, not a release or generalization certificate.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path

from monitor.observer_runtime import LABELS
from training.shared_slm_data import ROOT, sha

SOURCE = ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2"
DESTINATION = ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261118-demo01"
SEED = 3418


def balanced_rows(rows, count):
    """Deterministic class balance, cycling source recordings before reuse."""
    buckets = defaultdict(lambda: defaultdict(list))
    for row in rows:
        key = (row["kind"], row["target"]["label"])
        groups = tuple(sorted(row["provenance"]["source_recordings"]))
        buckets[key][groups].append(row)
    rng = random.Random(SEED)
    classes = {}
    for kind, labels in LABELS.items():
        for label in labels:
            key = (kind, label)
            groups = buckets[key]
            if sum(map(len, groups.values())) < count:
                raise ValueError(f"Insufficient distinct cases: {key}")
            for group in groups.values():
                group.sort(key=lambda row: row["input_sha256"])
                rng.shuffle(group)
            ordered = []
            while len(ordered) < count:
                for group in sorted(groups):
                    if groups[group] and len(ordered) < count:
                        ordered.append(groups[group].pop())
            classes[key] = ordered
    return [classes[key][index] for index in range(count) for key in sorted(classes)]


def assessment(results):
    from training.shared_slm_native import metrics
    measured = metrics(results)
    recalls = []
    precisions = []
    for kind, labels in LABELS.items():
        for label in labels:
            values = measured[kind].get("per_class", {}).get(label, {})
            recalls.append(values.get("recall", 0))
            precisions.append(values.get("precision", 0))
    invalid = sum(row["candidate"] is None for row in results)
    fp = measured["telemetry"]["false_positive_rate"]
    passed = (min(recalls) >= .75 and min(precisions) >= .60
              and measured["macro_f1"] >= .85 and fp <= .25 and invalid == 0)
    return {"metrics": measured, "demo_target_met": passed,
            "minimum_class_recall": min(recalls), "minimum_class_precision": min(precisions),
            "invalid_outputs": invalid,
            "selection_rank": [int(passed), min(recalls), measured["macro_f1"], -fp]}


def run(destination):
    import torch

    from training.shared_slm_native import (
        BASE,
        assistant_loss,
        encode,
        environment,
        load_rows,
        log,
        model_for,
        seed_all,
        tokenizer_for,
        write,
    )
    from training.slm_artifact_tools import batch_generate

    if destination.resolve() == SOURCE.resolve() or destination.resolve().is_relative_to(SOURCE.resolve()):
        raise ValueError("Demo output must be outside the preserved source run")
    destination.mkdir(parents=True, exist_ok=False)
    seed_all(SEED)
    source_selected = json.loads((SOURCE / "training/selected.json").read_text())
    adapter = Path(source_selected["checkpoint"])
    original_hash = sha(adapter / "adapter_model.safetensors")
    train = balanced_rows(load_rows(SOURCE / "data-v2", "train"), 12)
    validation = balanced_rows(load_rows(SOURCE / "data-v2", "val"), 8)
    if {r["input_sha256"] for r in train} & {r["input_sha256"] for r in validation}:
        raise ValueError("Train/validation input overlap")
    train_groups = {g for r in train for g in r["provenance"]["source_recordings"]}
    val_groups = {g for r in validation for g in r["provenance"]["source_recordings"]}
    if train_groups & val_groups:
        raise ValueError("Train/validation recording overlap")
    for name, rows in (("train", train), ("validation", validation)):
        (destination / f"{name}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    baseline_file = adapter / "validation.json"
    baseline = json.loads(baseline_file.read_text())
    lookup = {r["input_sha256"]: r for r in baseline["rows"]}
    selected_inputs = {r["input_sha256"] for r in validation}
    if not selected_inputs <= lookup.keys():
        raise ValueError("Baseline report missing fixed demo validation cases")
    baseline_rows = [lookup[r["input_sha256"]] for r in validation]
    policy = {"purpose": "development demo only", "seed": SEED,
              "source_adapter": str(adapter), "source_adapter_sha256": original_hash,
              "source_validation_sha256": sha(baseline_file),
              "source_data_manifest_sha256": sha(SOURCE / "data-v2/manifest.json"),
              "train_sha256": sha(destination / "train.jsonl"),
              "validation_sha256": sha(destination / "validation.jsonl"),
              "code_sha256": sha(__file__), "train_examples": 96, "validation_examples": 64,
              "epochs_max": 1, "optimizer_steps_max": 24, "gradient_accumulation": 4,
              "learning_rate": 2e-5, "label_loss_weight": 10,
              "validate_every_steps": 8, "max_new_tokens": 128,
              "wall_budget_seconds": 2400,
              "target": {"macro_f1_min": .85, "each_class_recall_min": .75,
                         "each_class_precision_min": .60, "telemetry_false_positive_rate_max": .25,
                         "invalid_outputs_max": 0},
              "selection": "target passed, then minimum class recall, then macro-F1, then fewer false alarms",
              "fixed_values_or_rule_based_predictions": False,
              "sealed_test_access": False, "release_qualified": False, "defaults_changed": False}
    write(destination / "policy.json", policy)
    write(destination / "environment.json", environment())
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; preserving prepared plan, no CPU training fallback")
    tokenizer = tokenizer_for(BASE)
    encoded = [encode(tokenizer, row) for row in train]
    if any(len(row["ids"]) > 3072 or row["prefix"] + 192 > 3072 for row in encoded):
        raise ValueError("Input too long; truncation prohibited")
    best = assessment(baseline_rows)
    write(destination / "baseline-validation.json", {**best, "rows": baseline_rows})
    selected = {"checkpoint": str(adapter), "step": 0, "assessment": best,
                "adapter_sha256": original_hash, "initialization_reused": True}
    write(destination / "selected.json", selected)
    model = model_for(BASE, adapter, trainable=True)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                                 lr=2e-5, weight_decay=.01)
    started = time.perf_counter()
    step = 0
    reason = "step_budget"
    for step in range(1, 25):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        losses = []
        for item in encoded[(step-1)*4:step*4]:
            loss = assistant_loss(model, item, 10)
            if not torch.isfinite(loss):
                raise RuntimeError("Non-finite loss")
            losses.append(loss.item())
            (loss/4).backward()
        grad = torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.)
        if not math.isfinite(grad.item()) or grad.item() <= 0:
            raise RuntimeError("Invalid gradient")
        optimizer.step()
        log(destination / "steps.jsonl", {"step": step, "loss": sum(losses)/4,
            "gradient_norm": grad.item(), "elapsed_s": time.perf_counter()-started})
        torch.cuda.empty_cache()
        if step % 8 == 0:
            checkpoint = destination / f"checkpoint-{step}"
            model.save_pretrained(str(checkpoint))
            tokenizer.save_pretrained(str(checkpoint))
            torch.save({"optimizer": optimizer.state_dict(), "step": step}, checkpoint / "optimizer.pt")
            results = batch_generate(model, tokenizer, validation, 4, checkpoint / "validation.rows.jsonl")
            checked = assessment(results)
            write(checkpoint / "validation.json", {**checked, "rows": results})
            if checked["selection_rank"] > best["selection_rank"]:
                best = checked
                selected = {"checkpoint": str(checkpoint), "step": step, "assessment": checked,
                            "adapter_sha256": sha(checkpoint / "adapter_model.safetensors"),
                            "initialization_reused": False}
                write(destination / "selected.json", selected)
            print(json.dumps({"step": step, "validation_macro_f1": checked["metrics"]["macro_f1"],
                              "minimum_recall": checked["minimum_class_recall"],
                              "demo_target_met": checked["demo_target_met"]}), flush=True)
            if checked["demo_target_met"]:
                reason = "demo_target_met"
                break
            if time.perf_counter()-started >= 2400:
                reason = "time_budget_at_checkpoint"
                break
    assert sha(adapter / "adapter_model.safetensors") == original_hash
    write(destination / "completed.json", {"steps": step, "stop_reason": reason,
        "elapsed_s": time.perf_counter()-started, "selected": selected,
        "source_preserved": True, "release_qualified": False, "defaults_changed": False,
        "limitations": "64 previously used validation cases; selected on development data, no independent final test."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=DESTINATION)
    args = parser.parse_args()
    run(args.destination)


if __name__ == "__main__":
    main()
