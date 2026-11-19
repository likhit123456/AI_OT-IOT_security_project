"""Prepare or train an opt-in multitask candidate without touching deployed weights.

The default stage only prepares recording-separated train/validation examples.
The sealed unseen file is never read. Train requires the training environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from monitor.observer_runtime import TASKS, parse_verdict, task_messages

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "training/observer_clean"
SEED = 3407


def prepare(destination):
    destination.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    recordings = {kind: set(value["recordings"]) for kind, value in manifest["splits"].items()}
    if manifest["strategy"] != "whole-source-recording" or any(
        a & b for i, a in enumerate(recordings.values()) for b in list(recordings.values())[i+1:]
    ):
        raise ValueError("recording separation required")
    seen = set()
    provenance = {"seed": SEED, "teacher_predictions_used": False, "sealed_test_read": False,
                  "source_manifest_sha256": hashlib.sha256((SOURCE / "manifest.json").read_bytes()).hexdigest(),
                  "splits": {}}
    rng = random.Random(SEED)
    # Validation takes priority over training when checking duplicate user inputs.
    for split in ("val", "train"):
        source = SOURCE / f"observer_v3_{split}.jsonl"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != manifest["splits"][split]["sha256"]:
            raise ValueError(f"changed source split: {split}")
        pools = defaultdict(list)
        for line in source.read_text(encoding="utf-8").splitlines():
            messages = json.loads(line)["messages"]
            raw = messages[1]["content"]
            key = json.dumps(json.loads(raw), sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            kind = json.loads(raw)["input"]
            verdict = json.loads(messages[2]["content"])
            identity = f"train:{TASKS[kind]}:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
            value = {k: verdict[k] for k in ("label", "score", "reason")}
            value["request_id"] = identity
            parse_verdict(json.dumps(value), kind, identity)
            pools[(kind, value["label"])].append({"messages": task_messages(kind, raw, identity) +
                                                  [{"role": "assistant", "content": json.dumps(value)}]})
        # Equal class support for training. Validation retains its natural counts.
        count = min(map(len, pools.values()))
        rows = []
        for pool in pools.values():
            rng.shuffle(pool)
            rows.extend(pool[:count] if split == "train" else pool)
        rng.shuffle(rows)
        output = destination / f"{split}.jsonl"
        output.write_text("".join(json.dumps(row)+"\n" for row in rows), encoding="utf-8")
        provenance["splits"][split] = {"source_sha256": digest,
                                      "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                                      "n": len(rows), "classes": dict(Counter(
                                          json.loads(r["messages"][2]["content"])["label"] for r in rows))}
    (destination / "manifest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")


def train_unsloth_legacy(destination, base, max_steps):
    # Optional imports keep preparation/evaluation runnable without a GPU stack.
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    for split in ("train", "val"):
        if hashlib.sha256((destination / f"{split}.jsonl").read_bytes()).hexdigest() != manifest["splits"][split]["output_sha256"]:
            raise ValueError("prepared dataset changed")
    model, tokenizer = FastLanguageModel.from_pretrained(base, max_seq_length=3072, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=16, lora_dropout=0,
               target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
               use_gradient_checkpointing="unsloth", random_state=SEED)
    data = load_dataset("json", data_files={k: str(destination / f"{k}.jsonl") for k in ("train", "val")})
    data = data.map(lambda row: {"text": tokenizer.apply_chat_template(row["messages"], tokenize=False)})
    trainer = SFTTrainer(model=model, processing_class=tokenizer,
              train_dataset=data["train"], eval_dataset=data["val"],
              args=SFTConfig(output_dir=str(destination / "checkpoints"), dataset_text_field="text",
                  max_length=3072, per_device_train_batch_size=1, gradient_accumulation_steps=8,
                  max_steps=max_steps, learning_rate=2e-4, seed=SEED, report_to="none",
                  eval_strategy="steps", eval_steps=50, save_steps=50))
    trainer.train()
    model.save_pretrained(str(destination / "adapter"))
    tokenizer.save_pretrained(str(destination / "adapter"))
    model.save_pretrained_gguf(str(destination / "gguf"), tokenizer, quantization_method="q4_k_m")
    (destination / "training-config.json").write_text(json.dumps({"base": base, "max_steps": max_steps,
               "seed": SEED, "context": 3072, "quantization": "q4_k_m"}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "prepare-native", "train", "smoke"), default="prepare")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--base")
    parser.add_argument("--backend", choices=("native", "unsloth-legacy"), default="native")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--validation-batch-size", type=int, default=4)
    parser.add_argument("--label-loss-weight", type=float, default=1.)
    parser.add_argument("--initial-validation-report", type=Path)
    parser.add_argument("--stop-after-step", type=int)
    args = parser.parse_args()
    if args.max_steps is not None and args.max_steps < 1:
        parser.error("max-steps must be positive")
    if args.stage == "prepare":
        prepare(args.destination)
    elif args.stage == "prepare-native":
        from training.shared_slm_data import prepare as prepare_native
        prepare_native(args.destination)
    elif args.backend == "unsloth-legacy":
        train_unsloth_legacy(args.destination, args.base or "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit", args.max_steps or 200)
    else:
        from training.shared_slm_native import BASE, train
        if args.max_steps is not None:
            parser.error("native training uses its recorded epoch budget; max-steps applies only to unsloth-legacy")
        base = BASE if args.base in (None, "Qwen/Qwen2.5-0.5B-Instruct") else Path(args.base)
        train(args.destination, base, args.data or args.destination / "data-v1", args.stage == "smoke", args.resume,
              args.validation_batch_size, args.label_loss_weight, args.initial_validation_report, args.stop_after_step)
