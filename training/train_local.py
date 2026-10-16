"""
Local QLoRA fine-tune of the L3 operator on an RTX 3050 (6 GB), adapted from
the Colab notebook (Downloads/scram_operator_finetune.ipynb).

  base     Qwen2.5-1.5B-Instruct, 4-bit (Unsloth bnb-4bit), LoRA r=16
  data     training/train.jsonl, training/val.jsonl (real GRFICS states, expert labels)
  format   every example is checked against l3_brain/prompt_format (the same
           system prompt + state JSON LLMOperator serves), then rendered with
           the Qwen chat template - train == serve
  6 GB     batch 1 x grad-accum 8, max_seq_length 1024 (examples are ~520
           tokens), gradient checkpointing on, 1 epoch (3 epochs overfit)
  eval     on val: valid-JSON rate, per-setpoint mean abs error, hold accuracy
  export   adapter -> merged 16-bit -> GGUF f16 (llama.cpp convert_hf_to_gguf.py)
           -> q4_k_m (llama-quantize) in model/, plus an Ollama Modelfile

Outputs (all under model/; weights are gitignored):
  model/scram-operator-lora/      LoRA adapter + tokenizer
  model/train_log.json            loss history + run config
  model/eval_val.json             eval metrics + per-example predictions
  model/scram-operator-gguf/      scram-operator-q4_k_m.gguf + Modelfile

Stages (re-runnable; later stages load the saved adapter):
  python -m training.train_local                  # train, eval, export
  python -m training.train_local --stage eval     # or: export
  python -m training.train_local --max-seq 768    # if it OOMs at 1024
  (an interrupted train stage resumes from model/_trainer/checkpoint-*; delete that
  folder to start fresh)

Run with the training venv: .venv-train\\Scripts\\python -m training.train_local
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from l3_brain.policy import parse_llm_targets                           # noqa: E402
from l3_brain.prompt_format import state_to_json, system_prompt         # noqa: E402

BASE = "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit"
TRAIN, VAL = REPO / "training" / "train.jsonl", REPO / "training" / "val.jsonl"
MODEL_DIR = REPO / "model"
ADAPTER = MODEL_DIR / "scram-operator-lora"
CKPT = MODEL_DIR / "_trainer"                  # checkpoints every 25 steps (resume)
MERGED = MODEL_DIR / "_merged-16bit"            # intermediate; deleted after GGUF export
GGUF_DIR = MODEL_DIR / "scram-operator-gguf"
LLAMA_CPP = REPO / ".tools" / "llama.cpp"         # convert_hf_to_gguf.py + llama-quantize.exe
KEYS = ("pressure_sp", "level_sp", "production_rate_sp", "a_comp_sp")
SEED = 3407


def load_examples(path: Path) -> list:
    """Read a split and enforce train == serve against l3_brain/prompt_format."""
    sys_msg = system_prompt()
    out = []
    for line in path.open():
        msgs = json.loads(line)["messages"]
        assert [m["role"] for m in msgs] == ["system", "user", "assistant"], path
        assert msgs[0]["content"] == sys_msg, f"{path}: system prompt differs from prompt_format"
        assert msgs[1]["content"] == state_to_json(json.loads(msgs[1]["content"])), \
            f"{path}: user turn is not prompt_format.state_to_json"
        out.append(msgs)
    return out


def load_model(max_seq: int, adapter: bool = False):
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import get_chat_template
    model, tok = FastLanguageModel.from_pretrained(
        model_name=str(ADAPTER) if adapter else BASE,
        max_seq_length=max_seq, load_in_4bit=True, dtype=None)
    tok = get_chat_template(tok, chat_template="qwen-2.5")
    return model, tok


# ---- train ---------------------------------------------------------------------------

def train(a) -> None:
    import torch
    from datasets import Dataset
    from transformers import TrainerCallback
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import train_on_responses_only

    model, tok = load_model(a.max_seq)
    model = FastLanguageModel.get_peft_model(
        model, r=16, lora_alpha=16, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=SEED)
    model.print_trainable_parameters()

    def to_ds(examples):
        texts = [tok.apply_chat_template(m, tokenize=False, add_generation_prompt=False) for m in examples]
        return Dataset.from_dict({"text": texts})
    train_ds, val_ds = to_ds(load_examples(TRAIN)), to_ds(load_examples(VAL))
    lengths = [len(tok(t)["input_ids"]) for t in train_ds["text"]]
    print(f"train {len(train_ds)}  val {len(val_ds)}  tokens/example max {max(lengths)} "
          f"(max_seq {a.max_seq})", flush=True)
    assert max(lengths) <= a.max_seq, "an example would be truncated; raise --max-seq"

    history = []

    class LogCB(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kw):
            if logs:
                history.append({"step": state.global_step, **logs})
                print(json.dumps({"step": state.global_step, **logs}), flush=True)

    bf16 = torch.cuda.is_bf16_supported()
    steps_per_epoch = math.ceil(len(train_ds) / 8)
    trainer = SFTTrainer(
        model=model, processing_class=tok, train_dataset=train_ds, eval_dataset=val_ds,
        callbacks=[LogCB()],
        args=SFTConfig(
            dataset_text_field="text", max_length=a.max_seq,
            per_device_train_batch_size=1, gradient_accumulation_steps=8,
            per_device_eval_batch_size=1,
            warmup_steps=5, num_train_epochs=1, learning_rate=2e-4,
            fp16=not bf16, bf16=bf16, optim="adamw_8bit", weight_decay=0.01,
            lr_scheduler_type="linear", seed=SEED,
            logging_steps=10, eval_strategy="steps", eval_steps=max(1, steps_per_epoch // 4),
            dataloader_num_workers=0, dataset_num_proc=1,
            output_dir=str(CKPT), save_strategy="steps", save_steps=25, save_total_limit=1,
            report_to="none"))
    trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\n",
                                      response_part="<|im_start|>assistant\n")
    t0 = time.time()
    # resume from the last checkpoint if a previous run was interrupted
    resume = any(CKPT.glob("checkpoint-*"))
    if resume:
        print(f"resuming from {max(CKPT.glob('checkpoint-*'), key=lambda c: int(c.name.split('-')[1]))}",
              flush=True)
    stats = trainer.train(resume_from_checkpoint=resume or None)
    final_eval = trainer.evaluate()
    model.save_pretrained(str(ADAPTER))
    tok.save_pretrained(str(ADAPTER))
    log = {"base": BASE, "lora_r": 16, "max_seq": a.max_seq, "batch": 1, "grad_accum": 8,
           "epochs": 1, "lr": 2e-4, "train_examples": len(train_ds), "val_examples": len(val_ds),
           "train_loss": stats.training_loss, "final_eval_loss": final_eval.get("eval_loss"),
           "runtime_s": round(time.time() - t0, 1),
           "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2), "history": history}
    (MODEL_DIR / "train_log.json").write_text(json.dumps(log, indent=1))
    print(f"train_loss {stats.training_loss:.4f}  final eval_loss {final_eval.get('eval_loss'):.4f}  "
          f"peak VRAM {log['peak_vram_gb']} GB  {log['runtime_s']}s", flush=True)
    del trainer, model
    torch.cuda.empty_cache()


# ---- eval (notebook section 5, plus hold accuracy) ------------------------------------

def rule_of(rationale: str) -> str:
    from training.build_dataset import rule_of as r
    return r(rationale)


def evaluate(a) -> None:
    import torch
    from unsloth import FastLanguageModel
    model, tok = load_model(a.max_seq, adapter=True)
    FastLanguageModel.for_inference(model)
    examples = load_examples(VAL)
    if a.eval_n:
        examples = examples[:a.eval_n]

    n_valid = n_parser_ok = 0
    errs = defaultdict(list)                       # setpoint -> abs errors (both numeric)
    hold_tp = hold_gold = hold_fp = act_gold = 0
    per_rule = defaultdict(lambda: Counter())
    preds = []
    t0 = time.time()
    for i, msgs in enumerate(examples):
        gold = json.loads(msgs[2]["content"])
        rule = rule_of(gold["rationale"])
        prompt = tok.apply_chat_template(msgs[:2], tokenize=False, add_generation_prompt=True)
        ids = tok(prompt, return_tensors="pt").to("cuda")
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=160, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        text = tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
        pred = None
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                pred = json.loads(m.group(0))
            except ValueError:
                pred = None
        valid = isinstance(pred, dict) and all(k in pred for k in KEYS)
        targets, _ = parse_llm_targets(text)        # what LLMOperator would actually accept
        n_parser_ok += targets is not None
        per_rule[rule]["n"] += 1
        if valid:
            n_valid += 1
            per_rule[rule]["valid"] += 1
            gold_hold = all(gold[k] is None for k in KEYS)
            pred_hold = all(pred[k] is None for k in KEYS)
            if gold_hold:
                hold_gold += 1
                hold_tp += pred_hold
                per_rule[rule]["correct_hold"] += pred_hold
            else:
                act_gold += 1
                hold_fp += pred_hold
                for k in KEYS:
                    try:
                        e = abs(float(pred[k]) - float(gold[k]))
                        errs[k].append(e)
                        errs[(rule, k)].append(e)
                    except (TypeError, ValueError):
                        pass
        preds.append({"rule": rule, "gold": gold, "raw": text[:400], "valid": valid})
        if (i + 1) % 25 == 0:
            print(f"  eval {i + 1}/{len(examples)}  valid so far {n_valid}  "
                  f"{(time.time() - t0) / (i + 1):.1f}s/example", flush=True)

    mean = lambda v: round(sum(v) / len(v), 3) if v else None
    res = {
        "n": len(examples),
        "valid_json_all_4_setpoints": n_valid, "valid_json_rate": round(n_valid / len(examples), 4),
        "accepted_by_LLMOperator_parser": n_parser_ok,
        "mae": {k: mean(errs[k]) for k in KEYS},
        "mae_n": {k: len(errs[k]) for k in KEYS},
        "hold_recall": f"{hold_tp}/{hold_gold}",
        "false_hold_on_action_states": f"{hold_fp}/{act_gold}",
        "per_rule": {r: {**c, "mae": {k: mean(errs[(r, k)]) for k in KEYS}} for r, c in per_rule.items()},
        "secs_per_example": round((time.time() - t0) / len(examples), 2),
        "predictions": preds,
    }
    (MODEL_DIR / "eval_val.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "predictions"}, indent=1), flush=True)
    del model
    torch.cuda.empty_cache()


# ---- export --------------------------------------------------------------------------

def export(a) -> None:
    import shutil
    import torch
    quantize = LLAMA_CPP / "bin" / "llama-quantize.exe"
    convert = LLAMA_CPP / "convert_hf_to_gguf.py"
    if not (quantize.exists() and convert.exists()):
        sys.exit(f"llama.cpp tools missing under {LLAMA_CPP} (need convert_hf_to_gguf.py and "
                 "bin/llama-quantize.exe)")
    model, tok = load_model(a.max_seq, adapter=True)
    model.save_pretrained_merged(str(MERGED), tok, save_method="merged_16bit")
    del model
    torch.cuda.empty_cache()

    GGUF_DIR.mkdir(parents=True, exist_ok=True)
    f16 = GGUF_DIR / "_scram-operator-f16.gguf"
    q4 = GGUF_DIR / "scram-operator-q4_k_m.gguf"
    subprocess.run([sys.executable, str(convert), str(MERGED), "--outtype", "f16",
                    "--outfile", str(f16)], check=True)
    subprocess.run([str(quantize), str(f16), str(q4), "q4_k_m"], check=True)
    f16.unlink()
    shutil.rmtree(MERGED, ignore_errors=True)

    q3 = '"""'
    (GGUF_DIR / "Modelfile").write_text(
        f"FROM ./{q4.name}\n"
        "PARAMETER temperature 0\n"
        "PARAMETER num_ctx 2048\n"
        f"SYSTEM {q3}{system_prompt()}{q3}\n")
    print(f"GGUF: {q4}  ({q4.stat().st_size / 1e6:.0f} MB)\nModelfile: {GGUF_DIR / 'Modelfile'}", flush=True)


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["all", "train", "eval", "export"], default="all")
    ap.add_argument("--max-seq", type=int, default=1024)
    ap.add_argument("--eval-n", type=int, default=0, help="limit eval examples (0 = all of val)")
    a = ap.parse_args()
    MODEL_DIR.mkdir(exist_ok=True)
    if a.stage in ("all", "train"):
        train(a)
    if a.stage in ("all", "eval"):
        evaluate(a)
    if a.stage in ("all", "export"):
        export(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
