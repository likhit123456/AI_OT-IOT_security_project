"""Actual paired GGUF evaluation on new validation or independently frozen test.

Final candidate and unchanged-specialist runs each have a separate one-run
identity marker. Only an interrupted identical run can resume. No historical
final-test file is opened, no thresholds change, and defaults are never edited.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from monitor import observer_v2_format as V2
from monitor import observer_v3_format as V3
from monitor.llama_cpp_transport import llama_cpp_post
from monitor.observer_runtime import LABELS, parse_verdict
from monitor.telemetry_evidence import VERSION
from training.collect_slm_evidence import RUN
from training.local_gguf_server import BINARY, URL, ensure, register
from training.shared_slm_data import ROOT, sha
from training.shared_slm_native import load_rows, metrics
from training.slm_artifact_tools import with_cpu


def source_rows(run, data, split, model, legacy):
    if split == "validation":
        return load_rows(data, "val")
    frozen = json.loads((run / "freeze.json").read_text(encoding="utf-8"))
    manifest = json.loads((run / "final-data-manifest.json").read_text(encoding="utf-8"))
    if frozen.get("wire_schema") != VERSION or not manifest.get("model_frozen_before_opening_test"):
        raise ValueError("new final test requires the evidence-v2 freeze")
    if sha(run / "final-data.jsonl") != manifest["sha256"]:
        raise ValueError("frozen independent test data changed")
    if not legacy and (model.resolve() != Path(frozen["model"]).resolve() or sha(model) != frozen["model_sha256"]):
        raise ValueError("final inference model differs from the frozen selected artifact")
    return [json.loads(line) for line in (run / "final-data.jsonl").read_text(encoding="utf-8").splitlines()]


def evaluate(run, data, model, output, split="validation", legacy=False, alias="scram-observer-evidence-v2-eval"):
    if output.exists():
        raise FileExistsError(output)
    rows = source_rows(run, data, split, model, legacy)
    if not rows:
        raise ValueError("evaluation input is empty")
    if len({r["input_sha256"] for r in rows}) != len(rows):
        raise ValueError("duplicate evaluation identities")
    rows.sort(key=lambda r: (r["kind"], r["input_sha256"]))
    entries = {alias: (model, 3072)}
    if legacy:
        entries = {"scram-observer-v2-eval": (ROOT / "model/scram-observer-gguf/scram-observer-v2-q4_k_m.gguf", 2048),
                   "scram-observer-v3-eval": (ROOT / "model/scram-observer-v3-gguf/scram-observer-v3-q4_k_m.gguf", 3072)}
    weights = {name: sha(path) for name, (path, _) in entries.items()}
    identity = {"variant": "legacy" if legacy else "candidate", "weights": weights,
                "input_sha256": sha(run / "final-data.jsonl" if split == "final" else data / "val.jsonl"),
                "output": str(output.resolve()), "binary_sha256": sha(BINARY), "generation": {
                    "temperature": 0, "num_predict": 128, "repeat_penalty": 1.1, "repeat_last_n": 3072, "seed": 3408}}
    marker = output.with_suffix(".started.json")
    record_file = output.with_suffix(".rows.jsonl")
    output.parent.mkdir(parents=True, exist_ok=True)
    if split == "final":
        marker = run / f"final-evaluation-{'legacy' if legacy else 'candidate'}.started.json"
    if marker.exists():
        if json.loads(marker.read_text(encoding="utf-8")) != identity:
            raise ValueError("evaluation identity changed; refusing another final run")
    else:
        if record_file.exists():
            raise ValueError("partial results have no evaluation identity marker")
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(identity, stream, indent=2)
    results = [json.loads(line) for line in record_file.read_text(encoding="utf-8").splitlines()] if record_file.exists() else []
    if len(results) > len(rows) or any((r["input_sha256"], r["gold"], r["task"]) !=
        (rows[i]["input_sha256"], rows[i]["target"]["label"], rows[i]["kind"]) for i, r in enumerate(results)):
        raise ValueError("partial results disagree with frozen case order")
    for name, (path, context) in entries.items():
        register(run, name, path, context)
    for index, row in enumerate(rows[len(results):], start=len(results)):
        kind = row["kind"]
        name = ("scram-observer-v2-eval" if kind == "proposals" else "scram-observer-v3-eval") if legacy else alias
        server = ensure(run, name)
        messages = (V2 if kind == "proposals" else V3).messages(json.dumps(row["input"])) if legacy else row["messages"][:2]
        start, body, raw = time.perf_counter(), {}, ""
        try:
            body = llama_cpp_post(URL+"/api/chat", {"model": name, "stream": False, "messages": messages,
                "options": {**identity["generation"], "num_ctx": entries[name][1]}}, 60)
            raw = body.get("message", {}).get("content", "")
            if body.get("error") or body.get("done_reason") == "length":
                raise ValueError("backend error or truncated response")
            if legacy:
                verdict, note = (V2 if kind == "proposals" else V3).parse(raw, labels=LABELS[kind])
                label = verdict["label"] if verdict else None
            else:
                verdict = parse_verdict(raw, kind, "local", False)
                label, note = verdict["label"], "ok"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            label, note = None, f"{type(exc).__name__}: {exc}"
        result = {"task": kind, "gold": row["target"]["label"], "candidate": label, "raw": raw,
                  "note": note, "input_sha256": row["input_sha256"], "latency_s": time.perf_counter()-start,
                  "evaluation_group": row.get("evaluation_group", "validation"), "model_alias": name,
                  "server_binary_sha256": server["binary_sha256"],
                  **{k: body.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration", "load_duration")}}
        with record_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result)+"\n")
        results.append(result)
        if (index+1) % 25 == 0 or index+1 == len(rows):
            print(json.dumps({"evaluation": output.name, "completed": index+1, "total": len(rows)}), flush=True)
    report = {"split": split, "variant": identity["variant"], "runtime": "local llama.cpp; exact binary identity recorded",
              "weights_sha256": None if legacy else sha(model), "weights": weights, "identity": identity,
              "metrics": metrics(results), "telemetry_paths": with_cpu(results, rows), "rows": results,
              "by_group": {group: metrics([r for r in results if r["evaluation_group"] == group])
                           for group in sorted({r["evaluation_group"] for r in results})},
              "generation": identity["generation"], "constrained_decoding": False, "plant_writes": False}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    if split == "final":
        with marker.with_name(marker.name.replace(".started.", ".completed.")).open("x", encoding="utf-8") as stream:
            json.dump({"report_sha256": sha(output), "identity": identity, "n": len(results)}, stream, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--data", type=Path, default=RUN / "data-v2")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("validation", "final"), default="validation")
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--alias", default="scram-observer-evidence-v2-eval")
    args = parser.parse_args()
    evaluate(args.run, args.data, args.model, args.output, args.split, args.legacy, args.alias)


if __name__ == "__main__":
    main()
