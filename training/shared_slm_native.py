"""Native Windows Transformers/PEFT build, with assistant-only loss and no uploads.

All model loads are offline from an explicitly pinned local base. No Unsloth,
bitsandbytes, WSL, Ollama, TRL Trainer or application-environment changes required.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import random
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

from monitor.observer_runtime import LABELS, parse_verdict
from training.shared_slm_data import ROOT, SEED, sha

REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
BASE = ROOT / "artifacts/optimization/candidates/base-qwen2.5-0.5b"
RUN = ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261102-v1"


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def log(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value)+"\n")
    print(json.dumps(value), flush=True)


def seed_all(seed=SEED):
    import torch
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(4)


def environment():
    import platform

    import psutil
    import torch
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": {p: importlib.metadata.version(p) for p in
                         ("torch", "transformers", "peft", "accelerate", "safetensors", "tokenizers", "huggingface_hub")},
            "cuda_runtime": torch.version.cuda, "cuda_available": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name() if torch.cuda.is_available() else None,
            "cuda_memory_free_total": torch.cuda.mem_get_info() if torch.cuda.is_available() else None,
            "ram": psutil.virtual_memory()._asdict(), "disk": shutil.disk_usage(ROOT)._asdict()}


def preflight(run):
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import Qwen2Config, Qwen2ForCausalLM

    seed_all()
    info = environment()
    write(run / "environment.json", info)
    frozen = subprocess.check_output([str(Path(__import__('sys').executable)), "-m", "pip", "freeze", "--all"], text=True)
    (run / "environment-freeze.txt").write_text(frozen, encoding="utf-8")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable")
    start = time.perf_counter()
    tensor = torch.randn(256, 256, device="cuda", requires_grad=True)
    loss = (tensor @ tensor).square().mean()
    loss.backward()
    tensor_test = {"loss": loss.item(), "gradient_norm": tensor.grad.norm().item()}
    del tensor, loss
    # Diagnostic random architecture, NOT the pretrained candidate or task smoke.
    config = Qwen2Config(vocab_size=512, hidden_size=128, intermediate_size=256,
                         num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2)
    model = Qwen2ForCausalLM(config).to(device="cuda", dtype=torch.bfloat16)
    model = get_peft_model(model, LoraConfig(r=4, lora_alpha=8, target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM"))
    model.train()
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=1e-3)
    before = {n: p.detach().clone() for n, p in model.named_parameters() if p.requires_grad}
    inputs = torch.randint(0, 512, (1, 128), device="cuda")
    result = model(inputs, labels=inputs, use_cache=False)
    result.loss.backward()
    grad = sum(p.grad.float().square().sum().item() for p in model.parameters() if p.grad is not None)**.5
    optimizer.step()
    changed = sum(not torch.equal(before[n], p.detach()) for n, p in model.named_parameters() if p.requires_grad)
    if not math.isfinite(result.loss.item()) or not grad > 0 or not changed:
        raise RuntimeError("native PEFT gradient/update diagnostic failed")
    model.eval()
    with torch.no_grad():
        sparse = assistant_loss(model, {"ids": inputs[0].tolist(), "prefix": 64})
        logits = model(inputs, use_cache=False).logits[:, :-1].float()
        labels = inputs[:, 1:].clone()
        labels[:, :63] = -100
        reference = torch.nn.functional.cross_entropy(logits.reshape(-1, 512), labels.reshape(-1), ignore_index=-100)
    if not torch.allclose(sparse, reference, atol=1e-5):
        raise RuntimeError("sparse assistant loss differs from standard masked loss")
    write(run / "preflight.json", {"tensor_test": tensor_test, "random_architecture_diagnostic_only": True,
          "pretrained_task_training_occurred": False, "lora_loss": result.loss.item(),
          "gradient_norm": grad, "changed_adapter_tensors": changed,
          "assistant_loss_matches_full_masked_reference": True,
          "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(), "elapsed_s": time.perf_counter()-start})


def base_ready(base):
    required = ("model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json", "LICENSE", "source-revision.json")
    missing = [name for name in required if not (base / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Pinned official base not ready: {missing}; run scripts/fetch_observer_slm_base.py")
    source = json.loads((base / "source-revision.json").read_text(encoding="utf-8"))
    if source["revision"] != REVISION or source["repo_id"] != "Qwen/Qwen2.5-0.5B-Instruct":
        raise ValueError("base provenance mismatch")


def tokenizer_for(base):
    from transformers import AutoTokenizer
    base_ready(base)
    return AutoTokenizer.from_pretrained(str(base), local_files_only=True)


def load_rows(data, split):
    path = data / f"{split}.jsonl"
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    if sha(path) != manifest["splits"][split]["sha256"]:
        raise ValueError("candidate dataset changed")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def encode(tokenizer, row):
    prefix = tokenizer.apply_chat_template(row["messages"][:2], tokenize=True, add_generation_prompt=True, return_dict=False)
    full = tokenizer.apply_chat_template(row["messages"], tokenize=True, add_generation_prompt=False, return_dict=False)
    if full[:len(prefix)] != prefix:
        raise ValueError("chat-template generation/assistant boundary mismatch")
    suffix = full[len(prefix):]
    if tokenizer.eos_token_id not in suffix:
        raise ValueError("assistant EOS missing")
    # Keep complete supervision through EOS, remove only the post-EOS separator.
    full = full[:len(prefix)+suffix.index(tokenizer.eos_token_id)+1]
    if len(full) <= len(prefix):
        raise ValueError("no assistant supervision")
    text = tokenizer.apply_chat_template(row["messages"], tokenize=False, add_generation_prompt=False)
    prefix_text = tokenizer.apply_chat_template(row["messages"][:2], tokenize=False, add_generation_prompt=True)
    match = re.search(r'"label"\s*:\s*"([^"]+)"', row["messages"][2]["content"])
    if not match or match.group(1) != row["target"]["label"]:
        raise ValueError("assistant class span missing")
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    if offsets["input_ids"][:len(full)] != full:
        raise ValueError("label offsets differ from chat-template tokenization")
    left, right = len(prefix_text)+match.start(1), len(prefix_text)+match.end(1)
    label_positions = [index-len(prefix) for index, (start, end) in enumerate(offsets["offset_mapping"][:len(full)])
                       if index >= len(prefix) and start < right and end > left]
    if not label_positions:
        raise ValueError("no class-label tokens to supervise")
    return {"ids": full, "prefix": len(prefix), "kind": row["kind"],
            "label": row["target"]["label"], "label_positions": label_positions, "row": row}


def token_audit(run, base, data):
    tokenizer = tokenizer_for(base)
    result = {"base_revision": REVISION, "tokenizer_sha256": sha(base / "tokenizer.json"),
              "template": tokenizer.chat_template, "eos_token": tokenizer.eos_token,
              "eos_token_id": tokenizer.eos_token_id, "truncation": False, "padding_supervised": False,
              "request_ids_in_model_input_or_target": False, "splits": {}}
    for split in ("train", "val"):
        encoded = [encode(tokenizer, row) for row in load_rows(data, split)]
        result["splits"][split] = {kind: {"n": len(rows), "max_total_tokens": max(len(r["ids"]) for r in rows),
            "max_assistant_tokens": max(len(r["ids"])-r["prefix"] for r in rows),
            "mean_total_tokens": sum(len(r["ids"]) for r in rows)/len(rows)}
            for kind in LABELS if (rows := [r for r in encoded if r["kind"] == kind])}
    write(run / "token-audit.json", result)
    return result


def model_for(base, adapter=None, trainable=False):
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import AutoModelForCausalLM
    base_ready(base)
    # Limit this process only. WDDM otherwise permits unused cached allocations
    # to spill into shared host memory even when live tensors fit on the GPU.
    torch.cuda.set_per_process_memory_fraction(.60)
    model = AutoModelForCausalLM.from_pretrained(str(base), local_files_only=True,
                 dtype=torch.bfloat16, attn_implementation="sdpa", low_cpu_mem_usage=True).to("cuda")
    model.config.use_cache = False
    if adapter:
        model = PeftModel.from_pretrained(model, str(adapter), is_trainable=trainable)
    elif trainable:
        model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0,
                   target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
                   task_type="CAUSAL_LM"))
    if trainable:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    return model


def assistant_loss(model, item, label_weight=1.):
    """Compute shifted next-token CE only for complete assistant response + EOS.

Output projection only at supervised positions avoids allocating a vocabulary
logit tensor for every long telemetry input token. No input token is truncated.
"""
    import torch
    ids = torch.tensor([item["ids"]], device="cuda")
    positions = torch.arange(item["prefix"]-1, len(item["ids"])-1, device="cuda")
    logits = model(input_ids=ids, use_cache=False, logits_to_keep=positions).logits
    target = ids[:, item["prefix"]:]
    if logits.shape[1] != target.shape[1]:
        raise ValueError("assistant loss alignment mismatch")
    if label_weight == 1:
        return torch.nn.functional.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), target.reshape(-1))
    losses = torch.nn.functional.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), target.reshape(-1), reduction="none")
    weights = torch.ones_like(losses)
    weights[item["label_positions"]] = label_weight
    return (losses*weights).sum()/weights.sum()


def validation_loss(model, items, batch_size=4):
    """Same assistant-only per-example loss, batching decoder work without truncation."""
    import torch
    model.eval()
    decoder = model.get_base_model() if hasattr(model, "get_base_model") else model
    losses = []
    with torch.no_grad():
        for start in range(0, len(items), batch_size):
            batch = items[start:start+batch_size]
            length = max(len(item["ids"]) for item in batch)
            ids = torch.zeros((len(batch), length), device="cuda", dtype=torch.long)
            attention = torch.zeros_like(ids)
            for index, item in enumerate(batch):
                ids[index, :len(item["ids"])] = torch.tensor(item["ids"], device="cuda")
                attention[index, :len(item["ids"])] = 1
            hidden = decoder.model(input_ids=ids, attention_mask=attention, use_cache=False).last_hidden_state
            states = torch.cat([hidden[index, item["prefix"]-1:len(item["ids"])-1]
                                for index, item in enumerate(batch)])
            targets = torch.cat([ids[index, item["prefix"]:len(item["ids"])]
                                 for index, item in enumerate(batch)])
            scores = decoder.lm_head(states).float()
            token_losses = torch.nn.functional.cross_entropy(scores, targets, reduction="none")
            offset = 0
            for item in batch:
                count = len(item["ids"])-item["prefix"]
                losses.append(token_losses[offset:offset+count].mean().item())
                offset += count
            del ids, attention, hidden, states, targets, scores, token_losses
            torch.cuda.empty_cache()
            if (start//batch_size) % 20 == 0:
                print(f"validation loss {min(start+batch_size,len(items))}/{len(items)}", flush=True)
    return sum(losses)/len(losses)


def metrics(rows):
    from training.evaluate_shared_observer import summarize
    result = {}
    f1s = []
    for kind in LABELS:
        subset = [row for row in rows if row["task"] == kind]
        report = summarize(subset, "candidate")
        report["latency_s_mean"] = sum(r["latency_s"] for r in subset)/len(subset) if subset else None
        for values in report.get("per_class", {}).values():
            p, r = values["precision"], values["recall"]
            f1s.append(2*p*r/(p+r) if p+r else 0)
        result[kind] = report
    result["macro_f1"] = sum(f1s)/len(f1s) if f1s else 0
    return result


def generate_rows(model, tokenizer, rows):
    import torch
    model.eval()
    results = []
    for index, row in enumerate(rows):
        prompt = tokenizer.apply_chat_template(row["messages"][:2], tokenize=True, add_generation_prompt=True,
                                               return_tensors="pt", return_dict=False).to("cuda")
        start = time.perf_counter()
        raw = ""
        try:
            with torch.inference_mode():
                output = model.generate(prompt, attention_mask=torch.ones_like(prompt), do_sample=False,
                         max_new_tokens=128, eos_token_id=tokenizer.eos_token_id,
                         pad_token_id=tokenizer.eos_token_id, use_cache=True)
            torch.cuda.synchronize()
            raw = tokenizer.decode(output[0, prompt.shape[1]:], skip_special_tokens=True)
            parsed = parse_verdict(raw, row["kind"], "bound-locally", include_identity=False)
            label, note = parsed["label"], "ok"
        except (ValueError, TypeError, RuntimeError) as exc:
            label, note = None, f"{type(exc).__name__}: {exc}"
        results.append({"task": row["kind"], "gold": row["target"]["label"], "candidate": label,
                        "note": note, "raw": raw, "input_sha256": row["input_sha256"],
                        "latency_s": time.perf_counter()-start})
        if (index+1) % 20 == 0:
            print(f"generation {index+1}/{len(rows)}", flush=True)
    return results


def train(run, base, data, smoke=False, resume=None, validation_batch_size=1,
          label_weight=1., initial_validation_report=None, stop_after_step=None, policy_path=None):
    if (run / "freeze.json").exists():
        raise RuntimeError("candidate is frozen; use a new versioned run for further training")
    policy = None
    if policy_path is not None:
        from training.slm_evidence_policy import validate_training_readiness
        policy = validate_training_readiness(data, policy_path)
        if label_weight != policy["training_budget"]["label_loss_weight"]:
            raise ValueError("label loss weight must match the declared evidence-v2 policy")
        if resume and not Path(resume).resolve().is_relative_to(run.resolve()):
            raise ValueError("evidence-v2 may resume only its own recovery checkpoints")
        if initial_validation_report or stop_after_step:
            raise ValueError("evidence-v2 policy disallows phase/objective changes")
    import torch
    seed = policy["training_budget"]["seed"] if policy else SEED
    seed_all(seed)
    tokenizer = tokenizer_for(base)
    audit = token_audit(run, base, data)
    raw_train, raw_val = load_rows(data, "train"), load_rows(data, "val")
    encoded = [encode(tokenizer, row) for row in raw_train]
    val_encoded = [encode(tokenizer, row) for row in raw_val]
    selection_rows = []
    per_class = Counter()
    for row in raw_val:
        key = (row["kind"], row["target"]["label"])
        if policy or per_class[key] < 12:
            selection_rows.append(row)
            per_class[key] += 1
    output = run / ("smoke" if smoke else "training")
    if policy and (output / "completed.json").exists():
        raise RuntimeError("declared evidence-v2 run already completed; refusing another training run")
    if policy and (output / "steps.jsonl").exists() and not resume:
        raise RuntimeError("evidence-v2 optimization already started; resume its recovery checkpoint")
    output.mkdir(parents=True, exist_ok=True)
    accumulation = 2 if smoke else 8
    epochs = 2
    total_steps = 16 if smoke else math.ceil(len(encoded)/accumulation)*epochs
    if policy and not smoke:
        total_steps = min(total_steps, policy["training_budget"]["optimizer_steps_max"])
    if policy and (max(len(x["ids"]) for x in encoded+val_encoded) > 3072
                   or any(x["prefix"]+192 > 3072 for x in encoded+val_encoded)):
        raise ValueError("evidence-v2 context budget exceeded; truncation is prohibited")
    config = {"base": str(base), "base_revision": REVISION, "seed": seed, "epochs_budget": epochs,
              "optimizer_steps_budget": total_steps, "gradient_accumulation": accumulation,
              "microbatch": 1, "precision": "bfloat16", "learning_rate": 2e-4,
              "cuda_allocator_fraction": .60, "release_unused_cache_each_step": True,
              "recovery_checkpoint_interval_steps": 10,
              "validation_batch_size": validation_batch_size,
              "class_token_loss_weight": label_weight,
              "stop_after_step": stop_after_step,
              "selection_loss_remains_unweighted": True,
              "initial_validation_report_sha256": sha(initial_validation_report) if initial_validation_report else None,
              "lora_r": 16, "lora_alpha": 32, "gradient_checkpointing": True,
              "supervision": "assistant response + EOS only; no padding or user loss",
              "max_context_observed": max(len(x["ids"]) for x in encoded+val_encoded),
              "checkpoint_interval_steps": 45, "selection": "highest fixed validation-subset macro-F1; tie lower validation loss",
              "validation_subset_per_class_max": 12, "early_stopping": "two consecutive non-improving scheduled evaluations",
              "smoke_only": smoke, "data_manifest_sha256": sha(data / "manifest.json"),
              "token_audit": audit["splits"]}
    if policy:
        config.update(experiment_policy_sha256=sha(policy_path),
                      selection="highest full fixed validation macro-F1; tie lower unweighted assistant loss",
                      validation_subset_per_class_max=None, representation=policy["representation"])
    config_path = output / "config.json"
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise ValueError("configuration changed; use a new versioned candidate directory")
    write(config_path, config)  # fixed before any candidate optimization
    model = model_for(base, resume, trainable=True)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=2e-4, weight_decay=.01)
    before = {n: p.detach().cpu().clone() for n, p in model.named_parameters() if p.requires_grad}
    start_step, best, no_improvement = 0, (-1., float("inf")), 0
    if resume:
        state = torch.load(Path(resume) / "optimizer.pt", map_location="cuda", weights_only=False)
        optimizer.load_state_dict(state["optimizer"])
        start_step, best, no_improvement = state["step"], tuple(state["best"]), state["no_improvement"]
        torch.set_rng_state(state["cpu_rng"].cpu())
        torch.cuda.set_rng_state_all([r.cpu() for r in state["cuda_rng"]])
        if (output / "selected.json").exists():
            # Continuing an interrupted run must not replace its best checkpoint
            # with the original step-45 baseline or mislabel recovery weights.
            previous = json.loads((output / "selected.json").read_text(encoding="utf-8"))
            best = (previous["validation_macro_f1"], previous["validation_loss"])
        elif initial_validation_report:
            report = json.loads(initial_validation_report.read_text(encoding="utf-8"))
            expected = {r["input_sha256"]: r["target"]["label"] for r in selection_rows}
            if {r["input_sha256"]: r["gold"] for r in report["rows"]} != expected:
                raise ValueError("initial selection report does not match the fixed validation subset")
            best, no_improvement = (metrics(report["rows"])["macro_f1"], report["loss"]), 0
            write(output / "selected.json", {"checkpoint": str(Path(resume).resolve()), "step": start_step,
                  "validation_macro_f1": best[0], "validation_loss": best[1], "frozen": False})
        elif validation_batch_size > 1:
            from training.slm_artifact_tools import batch_generate
            sample = val_encoded[:4]
            model.eval()
            with torch.no_grad():
                reference = sum(assistant_loss(model, item).item() for item in sample)/len(sample)
            batched = validation_loss(model, sample, validation_batch_size)
            if not math.isclose(reference, batched, rel_tol=.01, abs_tol=.01):
                raise RuntimeError("batched validation loss disagrees with original assistant mask")
            new_loss = validation_loss(model, val_encoded, validation_batch_size)
            generated = batch_generate(model, tokenizer, selection_rows, validation_batch_size,
                                       output / "resume-validation.rows.jsonl")
            quality = metrics(generated)
            best, no_improvement = (quality["macro_f1"], new_loss), 0
            write(output / "resume-validation.json", {"loss": new_loss, "metrics": quality,
                  "rows": generated, "mask_reference_loss": reference, "mask_batched_loss": batched})
            write(output / "selected.json", {"checkpoint": str(Path(resume).resolve()), "step": start_step,
                  "validation_macro_f1": best[0], "validation_loss": best[1], "frozen": False})
    rng = random.Random(seed)
    order = []
    for _ in range(max(epochs, math.ceil(total_steps*accumulation/len(encoded)))):
        indices = list(range(len(encoded)))
        rng.shuffle(indices)
        order.extend(indices)
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    model.train()
    for step in range(start_step, total_steps):
        optimizer.zero_grad(set_to_none=True)
        torch.cuda.empty_cache()
        losses = []
        for micro in range(accumulation):
            item = encoded[order[(step*accumulation+micro) % len(order)]]
            loss = assistant_loss(model, item, label_weight)
            if not torch.isfinite(loss):
                raise RuntimeError("non-finite training loss")
            losses.append(loss.item())
            (loss/accumulation).backward()
        grad = torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.)
        if not torch.isfinite(grad) or grad.item() <= 0:
            raise RuntimeError("non-finite or zero trainable gradient")
        progress = (step+1)/total_steps
        lr = 2e-4*min(1., (step+1)/max(1, int(.1*total_steps)))*(.1+.9*.5*(1+math.cos(math.pi*progress)))
        for group in optimizer.param_groups:
            group["lr"] = lr
        optimizer.step()
        torch.cuda.empty_cache()
        log(output / "steps.jsonl", {"step": step+1, "examples_seen": (step+1)*accumulation,
            "loss": sum(losses)/len(losses), "gradient_norm": grad.item(), "lr": lr,
            "elapsed_s": time.perf_counter()-started, "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved()})
        due = (step+1) % 45 == 0 or step+1 == total_steps
        if not due and (step+1) % 10 == 0:
            recovery = output / f"recovery-{step+1}"
            model.save_pretrained(str(recovery))
            tokenizer.save_pretrained(str(recovery))
            torch.save({"optimizer": optimizer.state_dict(), "step": step+1, "best": best,
                        "no_improvement": no_improvement, "cpu_rng": torch.get_rng_state(),
                        "cuda_rng": torch.cuda.get_rng_state_all()}, recovery / "optimizer.pt")
            write(output / "recovery.json", {"checkpoint": str(recovery), "step": step+1,
                  "adapter_sha256": sha(recovery / "adapter_model.safetensors")})
        if due:
            checkpoint = output / f"checkpoint-{step+1}"
            model.save_pretrained(str(checkpoint))
            tokenizer.save_pretrained(str(checkpoint))
            changed = sum(not torch.equal(before[n], p.detach().cpu()) for n, p in model.named_parameters() if p.requires_grad)
            current_loss = None
            if not smoke:
                model.eval()
                if validation_batch_size > 1:
                    from training.slm_artifact_tools import batch_generate
                    current_loss = validation_loss(model, val_encoded, validation_batch_size)
                    generated = batch_generate(model, tokenizer, selection_rows, validation_batch_size,
                                               checkpoint / "validation.rows.jsonl")
                else:
                    with torch.no_grad():
                        current_loss = sum(assistant_loss(model, item).item() for item in val_encoded)/len(val_encoded)
                    generated = generate_rows(model, tokenizer, selection_rows)
                quality = metrics(generated)
                write(checkpoint / "validation.json", {"loss": current_loss, "metrics": quality, "rows": generated})
                key = (quality["macro_f1"], current_loss)
                if key[0] > best[0] or (key[0] == best[0] and key[1] < best[1]):
                    best, no_improvement = key, 0
                    write(output / "selected.json", {"checkpoint": str(checkpoint), "step": step+1,
                          "validation_macro_f1": key[0], "validation_loss": key[1], "frozen": False})
                else:
                    no_improvement += 1
                model.train()
            torch.save({"optimizer": optimizer.state_dict(), "step": step+1, "best": best,
                        "no_improvement": no_improvement, "cpu_rng": torch.get_rng_state(),
                        "cuda_rng": torch.cuda.get_rng_state_all()}, checkpoint / "optimizer.pt")
            write(checkpoint / "integrity.json", {"changed_adapter_tensors": changed,
                  "adapter_sha256": sha(checkpoint / "adapter_model.safetensors"), "examples_seen": (step+1)*accumulation})
            if not changed:
                raise RuntimeError("adapter parameters did not change")
            if not smoke and no_improvement >= 2:
                break
            if stop_after_step and step+1 >= stop_after_step:
                break
    write(output / "completed.json", {"last_step": step+1, "elapsed_s": time.perf_counter()-started,
          "peak_allocated_bytes": torch.cuda.max_memory_allocated(), "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
          "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad), "smoke_only": smoke})


def evaluate(run, base, data, adapter, output):
    from transformers import AutoTokenizer
    seed_all()
    tokenizer = AutoTokenizer.from_pretrained(str(adapter or base), local_files_only=True)
    model = model_for(base, adapter)
    rows = generate_rows(model, tokenizer, load_rows(data, "val"))
    write(output, {"base_revision": REVISION, "adapter": str(adapter) if adapter else None,
          "split": "validation", "temperature": 0, "max_new_tokens": 128, "constrained_decoding": False,
          "metrics": metrics(rows), "rows": rows, "environment": environment()})


def merge(base, adapter, destination):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM
    base_ready(base)
    if destination.exists():
        raise FileExistsError(destination)
    model = AutoModelForCausalLM.from_pretrained(str(base), local_files_only=True, dtype=torch.bfloat16,
                                               low_cpu_mem_usage=True)
    model = PeftModel.from_pretrained(model, str(adapter)).merge_and_unload(safe_merge=True)
    model.save_pretrained(str(destination), safe_serialization=True)
    tokenizer_for(base).save_pretrained(str(destination))
    write(destination / "source-revision.json", {"base_revision": REVISION, "adapter": str(adapter),
          "adapter_sha256": sha(adapter / "adapter_model.safetensors")})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preflight", "tokens", "smoke", "reload-smoke", "train", "evaluate", "merge"))
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--validation-batch-size", type=int, default=1)
    parser.add_argument("--label-loss-weight", type=float, default=1.)
    parser.add_argument("--initial-validation-report", type=Path)
    parser.add_argument("--stop-after-step", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--policy", type=Path, help="Enforce the declared new evidence-v2 data/training policy")
    args = parser.parse_args()
    if not math.isfinite(args.label_loss_weight) or args.label_loss_weight < 1 or args.validation_batch_size < 1:
        parser.error("label loss weight must be finite and >=1; validation batch size must be positive")
    if args.initial_validation_report and not args.resume:
        parser.error("initial-validation-report requires a resumed checkpoint")
    args.run.mkdir(parents=True, exist_ok=True)
    data = args.data or args.run / "data-v1"
    if args.stage == "preflight":
        preflight(args.run)
    elif args.stage == "tokens":
        token_audit(args.run, args.base, data)
    elif args.stage in ("smoke", "train"):
        train(args.run, args.base, data, args.stage == "smoke", args.resume, args.validation_batch_size,
              args.label_loss_weight, args.initial_validation_report, args.stop_after_step, args.policy)
    elif args.stage == "reload-smoke":
        from transformers import AutoTokenizer
        completed = json.loads((args.run / "smoke/completed.json").read_text())
        adapter = args.run / f"smoke/checkpoint-{completed['last_step']}"
        tokenizer = AutoTokenizer.from_pretrained(str(adapter), local_files_only=True)
        model = model_for(args.base, adapter)
        rows = load_rows(data, "val")
        selected = [next(r for r in rows if r["kind"] == kind) for kind in LABELS]
        generated = generate_rows(model, tokenizer, selected)
        write(args.run / "smoke/reload-inference.json", {"fresh_process_reload": True,
              "adapter_sha256": sha(adapter / "adapter_model.safetensors"), "rows": generated})
    elif args.stage == "evaluate":
        evaluate(args.run, args.base, data, args.adapter, args.output or args.run / "validation-new.json")
    elif args.stage == "merge":
        if not args.adapter or not args.output:
            parser.error("merge requires --adapter and --output")
        merge(args.base, args.adapter, args.output)


if __name__ == "__main__":
    main()
