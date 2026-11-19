"""Offline real-model evaluation, conversion and freezing for a trained SLM.

No test split is opened until an explicit freeze file exists. No Ollama model
or historical artifact is overwritten. CLI evaluation uses separate processes,
so every task starts with fresh context and no HTTP server is needed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections import Counter
from pathlib import Path

from monitor import observer_v2_format as V2
from monitor import observer_v3_format as V3
from monitor.observer import _http_post_json
from monitor.observer_runtime import LABELS, parse_verdict, task_messages
from monitor.telemetry_classifier import approved_specialist
from training.shared_slm_data import ROOT, SEED, SOURCE, canonical, proposal_rows, sha
from training.shared_slm_native import (
    BASE,
    REVISION,
    environment,
    load_rows,
    metrics,
    model_for,
    seed_all,
    write,
)

RUN = ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261102-run04"
DATA = ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261102-v1/data-v1"
LLAMA = ROOT / ".tools/llama.cpp"
OLLAMA_URL = "http://127.0.0.1:11435"
LLAMA_SERVER = os.environ.get("SCRAM_SLM_EVAL_BACKEND", "llama_cpp") == "llama_cpp"
if LLAMA_SERVER:
    from monitor.llama_cpp_transport import llama_cpp_post
    from training.local_gguf_server import URL, ensure, register
    from training.local_gguf_server import residency as server_residency
    OLLAMA_URL = URL


def selected(run):
    return Path(json.loads((run / "training/selected.json").read_text())["checkpoint"]).resolve()


def subset(rows, per_class):
    if not per_class:
        return rows
    counts = Counter()
    chosen = []
    for row in rows:
        key = (row["kind"], row["target"]["label"])
        if counts[key] < per_class:
            chosen.append(row)
            counts[key] += 1
    return chosen


def batch_generate(model, tokenizer, rows, batch_size, destination):
    import torch
    tokenizer.padding_side = "left"
    model.eval()
    results = []
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start+batch_size]
        encoded = tokenizer.apply_chat_template([r["messages"][:2] for r in batch],
                   tokenize=True, add_generation_prompt=True, return_tensors="pt",
                   padding=True, return_dict=True).to("cuda")
        for index, row in enumerate(batch):
            expected = tokenizer.apply_chat_template(row["messages"][:2], tokenize=True,
                       add_generation_prompt=True, return_dict=False)
            actual = encoded["input_ids"][index][encoded["attention_mask"][index].bool()].tolist()
            if actual != expected:
                raise ValueError("batch padding changed the model input")
        began = time.perf_counter()
        finished = [False]*len(batch)
        try:
            eos_ids = model.generation_config.eos_token_id
            eos_ids = eos_ids if isinstance(eos_ids, list) else [eos_ids]
            with torch.inference_mode():
                outputs = model.generate(**encoded, do_sample=False, max_new_tokens=128,
                          max_time=45, use_cache=True, repetition_penalty=1.1, eos_token_id=eos_ids,
                          pad_token_id=tokenizer.pad_token_id)
            torch.cuda.synchronize()
            generated = outputs[:, encoded["input_ids"].shape[1]:]
            texts = tokenizer.batch_decode(generated, skip_special_tokens=True)
            finished = [any(token in eos_ids for token in item.tolist()) for item in generated]
        except RuntimeError as exc:
            texts = [f"inference_error: {exc}"]*len(batch)
        elapsed = time.perf_counter()-began
        for index, (row, raw) in enumerate(zip(batch, texts)):
            try:
                if not finished[index]:
                    raise ValueError("generation timeout/token budget reached without EOS")
                parsed = parse_verdict(raw, row["kind"], "bound-locally", False)
                label, note = parsed["label"], "ok"
            except (ValueError, TypeError) as exc:
                label, note = None, str(exc)
            result = {"task": row["kind"], "gold": row["target"]["label"], "candidate": label,
                      "raw": raw, "note": note, "input_sha256": row["input_sha256"],
                      "latency_s": elapsed, "batch_size": len(batch), "amortized_s": elapsed/len(batch)}
            results.append(result)
            with destination.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result)+"\n")
        del encoded
        if "outputs" in locals():
            del outputs
        if "generated" in locals():
            del generated
        torch.cuda.empty_cache()
        print(f"HF inference {min(start+batch_size,len(rows))}/{len(rows)}", flush=True)
    return results


def with_cpu(results, source_rows):
    detector = approved_specialist()
    inputs = {row["input_sha256"]: row["input"] for row in source_rows}
    for row in results:
        if row["task"] == "telemetry" and detector:
            try:
                prediction = detector.predict(inputs[row["input_sha256"]])
                row["cpu"] = prediction["label"]
                row["combined"] = prediction["label"]  # current runtime's independent advisory primary
            except (ValueError, TypeError, KeyError) as exc:
                row["cpu_error"] = str(exc)
    from training.evaluate_shared_observer import summarize
    telemetry = [r for r in results if r["task"] == "telemetry"]
    return {field: summarize(telemetry, field) for field in ("candidate", "cpu", "combined")}


def hf_eval(run, base, data, output, base_only=False, per_class=None, batch_size=4):
    from transformers import AutoTokenizer
    if output.exists():
        raise FileExistsError("refusing to replace an evaluation report")
    seed_all()
    rows = subset(load_rows(data, "val"), per_class)
    adapter = None if base_only else selected(run)
    tokenizer = AutoTokenizer.from_pretrained(str(adapter or base), local_files_only=True)
    start = time.perf_counter()
    model = model_for(base, adapter)
    load_s = time.perf_counter()-start
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_file = output.with_suffix(".rows.jsonl")
    if raw_file.exists():
        raise FileExistsError(raw_file)
    results = batch_generate(model, tokenizer, rows, batch_size, raw_file)
    import torch
    write(output, {"model": "base" if base_only else "fine_tuned_adapter", "adapter": str(adapter),
                  "base_revision": REVISION, "split": "validation", "constrained_decoding": False,
                  "batch_size": batch_size, "temperature": 0, "max_new_tokens": 128, "load_s": load_s,
                  "eos_token_ids": model.generation_config.eos_token_id,
                  "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(),
                  "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(),
                  "input_set_sha256": sha(data / "val.jsonl"), "n": len(rows),
                  "metrics": metrics(results), "telemetry_paths": with_cpu(results, rows),
                  "rows": results, "environment": environment()})


def export(run, base, quant_type="Q4_K_M", adapter=None):
    import torch

    from training.shared_slm_native import merge
    torch.set_num_threads(2)
    adapter = Path(adapter).resolve() if adapter is not None else selected(run)
    dest = run / "export"
    dest.mkdir(exist_ok=True)
    prior = dest / "tooling.json"
    if prior.exists() and json.loads(prior.read_text())["adapter_sha256"] != sha(adapter / "adapter_model.safetensors"):
        raise ValueError("export directory belongs to a different selected checkpoint; use a new versioned directory")
    merged = dest / "merged-bf16"
    if not merged.exists():
        merge(base, adapter, merged)
    converter = LLAMA / "convert_hf_to_gguf.py"
    executable = LLAMA / "bin/llama-quantize.exe"
    version = subprocess.run([str(LLAMA / "bin/llama-cli.exe"), "--version"],
                  capture_output=True, text=True, check=False)
    write(dest / "tooling.json", {"converter_sha256": sha(converter),
          "quantizer_sha256": sha(executable), "llama_version": version.stdout+version.stderr,
          "base_revision": REVISION, "adapter_sha256": sha(adapter / "adapter_model.safetensors")})
    f16 = dest / "scram-observer-slm-f16.gguf"
    q4 = dest / f"scram-observer-slm-{quant_type.lower()}.gguf"
    if not f16.exists():
        import sys
        with (dest / "conversion.log").open("w", encoding="utf-8") as stream:
            subprocess.run([sys.executable, str(converter), str(merged), "--outfile", str(f16), "--outtype", "f16"],
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
        print(f"F16 conversion complete: {f16}", flush=True)
    if not q4.exists():
        with (dest / f"quantization-{quant_type.lower()}.log").open("w", encoding="utf-8") as stream:
            subprocess.run([str(executable), str(f16), str(q4), quant_type, "4"],
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
        print(f"{quant_type} quantization complete: {q4}", flush=True)
    write(dest / "artifacts.json", [{"path": str(path.resolve()), "bytes": path.stat().st_size,
          "sha256": sha(path)} for path in [adapter / "adapter_model.safetensors", merged / "model.safetensors",
                                            *sorted(dest.glob("*.gguf"))]])


def extract_cli_response(transcript):
    marker = "\n\nAssistant:\n"
    if marker not in transcript:
        raise ValueError("llama-cli output file has no assistant transcript boundary")
    return transcript.split(marker, 1)[1].strip()


def cli_predict(model, messages, work, index):
    system_path = work / "system.txt"
    user_path = work / "user.txt"
    output_path = work / "response.txt"
    system_path.write_text(messages[0]["content"], encoding="utf-8")
    user_path.write_text(messages[1]["content"], encoding="utf-8")
    if output_path.exists():
        # Only this invocation's known scratch response, never a model/evidence file.
        output_path.write_text("", encoding="utf-8")
    command = [str(LLAMA / "bin/llama-cli.exe"), "-m", str(model), "-sysf", str(system_path),
               "-f", str(user_path), "--single-turn", "--no-display-prompt", "--simple-io",
               "--color", "off", "--log-disable", "--no-perf", "--no-warmup", "--no-escape",
               "-c", "3072", "-n", "128", "-t", "4", "-ngl", "0", "--temp", "0", "--top-k", "1",
               "--repeat-penalty", "1.1", "--repeat-last-n", "3072", "--seed", str(SEED), "--output-file", str(output_path)]
    start = time.perf_counter()
    process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=120, check=False)
    stdout = process.stdout
    raw_file = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    (work / f"raw-{index:05d}.json").write_text(json.dumps({"command": command, "exit_code": process.returncode,
          "stdout": stdout, "stderr": process.stderr, "output_file": raw_file}), encoding="utf-8")
    if process.returncode:
        raise RuntimeError(f"llama-cli exit {process.returncode}: {process.stderr[-600:]}")
    # --output-file is a User/Assistant transcript, not a raw response. Preserve
    # the entire assistant text, so malformed model output remains invalid.
    return extract_cli_response(raw_file), time.perf_counter()-start


def gguf_eval(run, data, model, output, per_class=12, legacy=False, final=False):
    if output.exists():
        raise FileExistsError(output)
    if final:
        if not (run / "freeze.json").exists():
            raise ValueError("freeze candidate/config before opening final test")
        rows = [json.loads(line) for line in (run / "final-data.jsonl").read_text(encoding="utf-8").splitlines()]
    else:
        rows = subset(load_rows(data, "val"), per_class)
    work = output.parent / (output.stem+"-raw")
    work.mkdir(parents=True, exist_ok=False)
    results = []
    for index, row in enumerate(rows):
        kind = row["kind"]
        messages = row["messages"][:2]
        if legacy:
            fmt = V2 if kind == "proposals" else V3
            messages = fmt.messages(json.dumps(row["input"]))
            actual_model = ROOT / ("model/scram-observer-gguf/scram-observer-v2-q4_k_m.gguf" if kind == "proposals"
                         else "model/scram-observer-v3-gguf/scram-observer-v3-q4_k_m.gguf")
        else:
            actual_model = model
        began = time.perf_counter()
        raw = ""
        try:
            raw, latency = cli_predict(actual_model, messages, work, index)
            if legacy:
                parsed, note = (V2 if kind == "proposals" else V3).parse(raw, labels=LABELS[kind])
                label = parsed["label"] if parsed else None
            else:
                parsed = parse_verdict(raw, kind, "bound-locally", False)
                label, note = parsed["label"], "ok"
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            label, note, latency = None, str(exc), time.perf_counter()-began
        result = {"task": kind, "gold": row["target"]["label"], "candidate": label,
                  "raw": raw, "note": note, "latency_s": latency, "input_sha256": row["input_sha256"]}
        results.append(result)
        with output.with_suffix(".rows.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result)+"\n")
        print(f"GGUF inference {index+1}/{len(rows)}: {kind} -> {label or 'invalid'}", flush=True)
    write(output, {"model": str(model), "model_sha256": sha(model) if not legacy else None,
                  "legacy_specialists": legacy, "runtime": "llama.cpp CPU CLI; fresh process per request",
                  "split": "test" if final else "validation", "constrained_decoding": False,
                  "temperature": 0, "max_new_tokens": 128, "per_class_cap": per_class,
                  "metrics": metrics(results), "telemetry_paths": with_cpu(results, rows), "rows": results})


def register_ollama(run, model, legacy=False, alias="scram-observer-slm-candidate"):
    entries = [(alias, model, 3072)]
    if legacy:
        entries = [("scram-observer-v2-eval", ROOT / "model/scram-observer-gguf/scram-observer-v2-q4_k_m.gguf", 2048),
                   ("scram-observer-v3-eval", ROOT / "model/scram-observer-v3-gguf/scram-observer-v3-q4_k_m.gguf", 3072)]
    if LLAMA_SERVER:
        for name, path, context in entries:
            register(run, name, path, context)
        write(run / f"llama-server/{'legacy' if legacy else alias}-registration.json",
              {"runtime": "llama.cpp fallback; Ollama manifest access denied", "entries": [
                  {"alias": name, "model": str(path), "sha256": sha(path), "context": context}
                  for name, path, context in entries]})
        return
    environment = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11435"}
    registration = []
    for name, path, context in entries:
        modelfile = run / "ollama" / f"{name}.Modelfile"
        modelfile.parent.mkdir(parents=True, exist_ok=True)
        modelfile.write_text(f'FROM "{path.resolve().as_posix()}"\nPARAMETER num_ctx {context}\n'
                            'PARAMETER temperature 0\nPARAMETER num_predict 128\nPARAMETER repeat_penalty 1.1\nPARAMETER repeat_last_n 3072\n', encoding="utf-8")
        command = ["ollama", "create", name, "-f", str(modelfile)]
        result = subprocess.run(command, env=environment, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180, check=False)
        entry = {"alias": name, "weights_sha256": sha(path), "command": command,
                 "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        registration.append(entry)
        if result.returncode:
            write(run / "ollama/registration-failed.json", registration)
            raise RuntimeError(result.stderr)
    write(run / ("ollama/legacy-registration.json" if legacy else f"ollama/{alias}-registration.json"), registration)


def ollama_eval(run, data, model, output, per_class=12, legacy=False, final=False,
                candidate_alias="scram-observer-slm-candidate"):
    import urllib.request
    record_file = output.with_suffix(".rows.jsonl")
    if output.exists() or (record_file.exists() and not final):
        raise FileExistsError("evaluation already exists; refusing to repeat/overwrite")
    if final:
        if (run / "final-evaluation.completed.json").exists():
            raise FileExistsError("the one frozen final evaluation is already complete")
        frozen = json.loads((run / "freeze.json").read_text())
        if sha(model) != frozen["model_sha256"]:
            raise ValueError("model changed since freeze")
        identity = {"model_sha256": frozen["model_sha256"], "test_sha256": sha(run / "final-data.jsonl"),
                    "output": str(output.resolve()), "alias": candidate_alias}
        marker = run / "final-evaluation.started.json"
        if marker.exists():
            if json.loads(marker.read_text(encoding="utf-8")) != identity:
                raise ValueError("final evaluation configuration changed; refusing a second test run")
        else:
            show = ensure(run, candidate_alias) if LLAMA_SERVER else _http_post_json(OLLAMA_URL+"/api/show", {"model": candidate_alias}, 10)
            if frozen["model_sha256"] not in (show.get("sha256", "") if LLAMA_SERVER else show.get("modelfile", "")):
                raise ValueError("Ollama alias does not point to the frozen GGUF blob")
            write(run / "final-alias-verification.json", show)
            write(marker, identity)
        rows = [json.loads(line) for line in (run / "final-data.jsonl").read_text(encoding="utf-8").splitlines()]
    else:
        rows = subset(load_rows(data, "val"), per_class)
    # Group comparison tasks to avoid repeatedly reloading the two legacy models.
    rows.sort(key=lambda row: row["kind"])
    results = [json.loads(line) for line in record_file.read_text(encoding="utf-8").splitlines()] if final and record_file.exists() else []
    if any(result["input_sha256"] != rows[index]["input_sha256"] for index, result in enumerate(results)):
        raise ValueError("partial final results do not match the frozen test order")
    residency = []
    output.parent.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(rows[len(results):], start=len(results)):
        kind = row["kind"]
        alias = ("scram-observer-v2-eval" if kind == "proposals" else "scram-observer-v3-eval") if legacy else candidate_alias
        if LLAMA_SERVER:
            ensure(run, alias)
        messages = ((V2 if kind == "proposals" else V3).messages(json.dumps(row["input"]))
                    if legacy else row["messages"][:2])
        start = time.perf_counter()
        body, raw = {}, ""
        try:
            post = llama_cpp_post if LLAMA_SERVER else _http_post_json
            body = post(OLLAMA_URL+"/api/chat", {"model": alias, "stream": False,
                "keep_alive": "30m", "messages": messages,
                "options": {"temperature": 0, "num_predict": 128, "repeat_penalty": 1.1, "repeat_last_n": 3072, "seed": SEED,
                            "num_ctx": 2048 if legacy and kind == "proposals" else 3072}}, 60)
            raw = body.get("message", {}).get("content", "")
            if body.get("error") or body.get("done_reason") == "length":
                raise ValueError(f"backend error/truncation: {body.get('error',body.get('done_reason'))}")
            if legacy:
                parsed, note = (V2 if kind == "proposals" else V3).parse(raw, labels=LABELS[kind])
                label = parsed["label"] if parsed else None
            else:
                parsed = parse_verdict(raw, kind, "bound-locally", False)
                label, note = parsed["label"], "ok"
        except (OSError, ValueError, TypeError, KeyError) as exc:
            label, note = None, f"{type(exc).__name__}: {exc}"
        result = {"task": kind, "gold": row["target"]["label"], "candidate": label,
                  "note": note, "raw": raw, "latency_s": time.perf_counter()-start,
                  "input_sha256": row["input_sha256"],
                  "evaluation_group": row.get("evaluation_group", "validation"),
                  **{key: body.get(key) for key in ("model", "prompt_eval_count", "eval_count", "load_duration", "total_duration")}}
        results.append(result)
        with record_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result)+"\n")
        if index % 25 == 0 or index+1 == len(rows):
            if LLAMA_SERVER:
                current = server_residency(run)
            else:
                with urllib.request.urlopen(OLLAMA_URL+"/api/ps", timeout=5) as response:
                    current = json.load(response)
            residency.append({"index": index, "task": kind, "response": current})
            print(f"Local GGUF evaluation {index+1}/{len(rows)}", flush=True)
    write(output, {"model": "legacy-specialists" if legacy else candidate_alias,
          "weights_sha256": sha(model) if not legacy else None,
          "runtime": "Ollama-bundled llama.cpp 0.8.0-dev 39febd2ad / CUDA v12; one loaded model at a time" if LLAMA_SERVER else "isolated Ollama 0.40.13 / CUDA; one loaded model at a time",
          "split": "sealed_test" if final else "validation", "temperature": 0,
          "max_new_tokens": 128, "repeat_penalty": 1.1, "repeat_last_n": 3072, "constrained_decoding": False,
          "metrics": metrics(results), "telemetry_paths": with_cpu(results, rows),
          "by_group": {group: metrics([r for r in results if r["evaluation_group"] == group])
                       for group in sorted({r["evaluation_group"] for r in results})},
          "residency_samples": residency, "rows": results})
    if final:
        write(run / "final-evaluation.completed.json", {"n": len(results), "report_sha256": sha(output),
              "model_sha256": sha(model), "test_sha256": sha(run / "final-data.jsonl")})


def freeze(run, data, model):
    if json.loads((data / "manifest.json").read_text(encoding="utf-8")).get("wire_schema") == "measured-evidence-v2":
        raise ValueError("evidence-v2 requires its independent sealed recordings; historical final-test builder is prohibited")
    path = run / "freeze.json"
    if path.exists():
        raise FileExistsError("candidate already frozen")
    write(path, {"base_revision": REVISION, "adapter": str(selected(run)),
                 "adapter_sha256": sha(selected(run) / "adapter_model.safetensors"),
                 "model": str(model), "model_sha256": sha(model), "context": 3072,
                 "wire_schema": "slm-v1", "generation": {"temperature": 0, "num_predict": 128},
                 "data_manifest_sha256": sha(data / "manifest.json"), "final_test_used_for_selection": False,
                 "quantization_selection": json.loads((run / "quantization-selection.json").read_text())
                     if (run / "quantization-selection.json").exists() else "explicit model supplied after validation",
                 "release_qualified": False})
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    source = SOURCE / "observer_v3_unseen.jsonl"
    if sha(source) != manifest["splits"]["unseen"]["sha256"]:
        raise ValueError("sealed test hash mismatch")
    rows = []
    for line in source.read_text(encoding="utf-8").splitlines():
        messages = json.loads(line)["messages"]
        user, target = json.loads(messages[1]["content"]), json.loads(messages[2]["content"])
        rows.append({"kind": "telemetry", "input": user, "target": target,
                     "evaluation_group": "historical_sealed_telemetry",
                     "provenance": {"label_method": "historical_whole_recording_test"}})
    new_proposals = proposal_rows("test", manifest["splits"]["unseen"]["recordings"], seed=SEED+1)
    for row in new_proposals:
        row["evaluation_group"] = "heldout_recording_proposals"
    # Predeclared synthetic stress procedure, executed only after freeze. These
    # correlated diagnostic variants do not increase release-gate class support.
    import copy
    import hashlib
    normal_parents = [r for r in rows if r["target"]["label"] == "normal"]
    stress = []
    for mode in ("frozen_level", "pressure_dropout"):
        eligible = [r for r in normal_parents if mode != "frozen_level" or
                    max(r["input"]["level"])-min(r["input"]["level"]) >= .1]
        for parent in eligible[:20]:
            modified = copy.deepcopy(parent)
            window = modified["input"]
            if mode == "frozen_level":
                window["level"] = [window["level"][0]]*len(window["level"])
                reason = "Measured level is frozen while the parent recording's level changes."
            else:
                window["pressure"][-2:] = [0., 0.]
                reason = "Measured pressure drops to zero in the last two samples."
            modified["target"] = {"label": "sensor_fault", "score": .8, "reason": reason}
            modified["evaluation_group"] = "synthetic_"+mode+"_stress"
            modified["provenance"] = {"label_method": "known measured-channel fault injection after model freeze",
                "parent_sha256": hashlib.sha256(canonical(parent["input"]).encode()).hexdigest(),
                "independent_recording_count_increased": False}
            stress.append(modified)
    rows += new_proposals+stress
    forbidden = {canonical(row["input"]) for split in ("train", "val") for row in load_rows(data, split)}
    seen = set(forbidden)
    final_rows = []
    for row in rows:
        key = canonical(row["input"])
        if key in seen:
            continue
        seen.add(key)
        row["messages"] = task_messages(row["kind"], json.dumps(row["input"]), "", False)+[
            {"role": "assistant", "content": json.dumps(row["target"], separators=(",", ":"))}]
        row["input_sha256"] = hashlib.sha256(key.encode()).hexdigest()
        final_rows.append(row)
    final_path = run / "final-data.jsonl"
    final_path.write_text("".join(json.dumps(row)+"\n" for row in final_rows), encoding="utf-8")
    write(run / "final-data-manifest.json", {"n": len(final_rows), "sha256": sha(final_path),
          "historical_test_sha256": sha(source), "proposal_generator_sha256": sha(ROOT / "training/shared_slm_data.py"),
          "proposal_seed": SEED+1, "counts": dict(Counter(f"{r['kind']}/{r['target']['label']}" for r in final_rows)),
          "counts_by_group": dict(Counter(r["evaluation_group"] for r in final_rows)),
          "synthetic_stress_used_to_meet_release_support_gate": False,
          "test_builder_sha256": sha(Path(__file__)),
          "overlap_or_duplicate_rows_removed": len(rows)-len(final_rows), "model_frozen_before_opening_test": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("hf-eval", "export", "gguf-eval", "register-local", "local-eval", "register-ollama", "ollama-eval", "freeze"))
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--alias", default="scram-observer-slm-candidate")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--per-class", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--quant-type", choices=("Q4_K_M", "Q8_0"), default="Q4_K_M")
    args = parser.parse_args()
    model = args.model or args.run / "export/scram-observer-slm-q4_k_m.gguf"
    if args.stage == "export":
        export(args.run, args.base, args.quant_type)
    elif args.stage == "freeze":
        freeze(args.run, args.data, model)
    elif args.stage in ("register-local", "register-ollama"):
        print("Registration backend: "+("llama.cpp direct local registry" if LLAMA_SERVER else "Ollama"), flush=True)
        register_ollama(args.run, model, args.legacy, args.alias)
    else:
        if not args.output:
            parser.error("evaluation requires --output")
        if args.stage == "hf-eval":
            hf_eval(args.run, args.base, args.data, args.output, args.base_only, args.per_class or None, args.batch_size)
        elif args.stage in ("local-eval", "ollama-eval"):
            ollama_eval(args.run, args.data, model, args.output, args.per_class or None, args.legacy, args.final, args.alias)
        else:
            gguf_eval(args.run, args.data, model, args.output, args.per_class or None, args.legacy, args.final)


if __name__ == "__main__":
    main()
