"""Offline demo inference on supplied JSON readings/proposals; no fixed answers."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from monitor.observer_runtime import parse_verdict
from monitor.telemetry_evidence import evidence_messages
from training.shared_slm_data import sha
from training.train_demo_observer import DESTINATION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DESTINATION)
    parser.add_argument("--inputs", type=Path, required=True,
                        help="A JSON input object, or a list of input objects")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence-review", action="store_true", help="Show combined evidence review alongside unchanged raw model output")
    args = parser.parse_args()
    if args.output and args.output.exists():
        raise FileExistsError(args.output)
    import torch

    from training.shared_slm_native import BASE, model_for, seed_all, tokenizer_for

    detector = None
    if args.evidence_review:
        from monitor.telemetry_classifier import approved_specialist
        detector = approved_specialist()

    selected = json.loads((args.run / "selected.json").read_text(encoding="utf-8"))
    adapter = Path(selected["checkpoint"])
    if sha(adapter / "adapter_model.safetensors") != selected["adapter_sha256"]:
        raise ValueError("Selected adapter identity changed")
    values = json.loads(args.inputs.read_text(encoding="utf-8"))
    values = values if isinstance(values, list) else [values]
    # Build from supplied inputs only. No labels, rationales or answers from a
    # validation report are consulted by the inference path.
    messages = [evidence_messages(value["input"], json.dumps(value)) for value in values]
    seed_all()
    tokenizer = tokenizer_for(BASE)
    model = model_for(BASE, adapter)
    model.eval()
    results = []
    for value, prompt in zip(values, messages):
        encoded = tokenizer.apply_chat_template(prompt, tokenize=True, add_generation_prompt=True,
                                                return_tensors="pt", return_dict=True).to("cuda")
        if encoded["input_ids"].shape[1] + 192 > 3072:
            raise ValueError("Complete input exceeds context; truncation prohibited")
        start = time.perf_counter()
        with torch.inference_mode():
            outputs = model.generate(**encoded, do_sample=False, max_new_tokens=128, max_time=45,
                                     use_cache=True, repetition_penalty=1.1,
                                     pad_token_id=tokenizer.pad_token_id)
        generated = outputs[0, encoded["input_ids"].shape[1]:].tolist()
        eos = model.generation_config.eos_token_id
        eos = eos if isinstance(eos, list) else [eos]
        raw = tokenizer.decode(generated, skip_special_tokens=True)
        result = {"input_kind": value["input"], "raw": raw,
                  "elapsed_s": time.perf_counter()-start}
        try:
            if not any(token in eos for token in generated):
                raise ValueError("Output did not finish within token/time budget")
            result["verdict"] = parse_verdict(raw, value["input"], "bound-locally", False)
        except (ValueError, TypeError) as exc:
            result["error"] = str(exc)
        if args.evidence_review:
            from monitor.evidence_review import proposal_review, telemetry_review
            raw_label = result.get("verdict", {}).get("label")
            if value["input"] == "proposals":
                result["evidence_review"] = proposal_review(value, raw_label)
            else:
                cpu = None
                if detector is not None:
                    try:
                        cpu = detector.predict(value)
                    except (ValueError, TypeError, KeyError, OverflowError) as exc:
                        result["cpu_error"] = str(exc)
                result["evidence_review"] = telemetry_review(value, raw_label, cpu)
        results.append(result)
        print(json.dumps(result), flush=True)
        torch.cuda.empty_cache()
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump({"adapter_sha256": selected["adapter_sha256"],
                       "input_file_sha256": sha(args.inputs), "fresh_process_reload": True,
                       "results": results, "evidence_review_enabled": args.evidence_review,
                       "release_qualified": False}, stream, indent=2)


if __name__ == "__main__":
    main()
