"""Select quantization on new validation, then freeze before opening new holdout.

This path never opens the historical observer_clean/unseen or run04 final data.
Support failures remain failures; no duplicate padding, split reassignment or
automatic change to application defaults is possible here.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from monitor.observer_runtime import LABELS
from monitor.telemetry_evidence import VERSION, evidence_messages
from training.collect_slm_evidence import RUN, collection_plans
from training.shared_slm_data import ROOT, canonical, sha
from training.shared_slm_native import metrics
from training.slm_evidence_data import proposals, read_recording, telemetry_row
from training.slm_evidence_policy import validate_training_readiness

POLICY = ROOT / "experiments/slm_evidence_v2_policy.json"
STRESS_POLICY = ROOT / "experiments/slm_evidence_v2_stress.json"


def stress_rows(parents, seen, policy):
    result, counts = [], {}
    rng = random.Random(policy["seed"])
    for variant in ("frozen_level", "pressure_dropout"):
        eligible = [row for row in parents if variant != "frozen_level" or
                    max(row["input"]["level"])-min(row["input"]["level"]) >= .1]
        rng.shuffle(eligible)
        counts[variant] = 0
        for parent in eligible:
            row = copy.deepcopy(parent)
            if variant == "frozen_level":
                row["input"]["level"] = [row["input"]["level"][0]]*16
                reason = "Measured level is identical across all 16 samples of the window."
            else:
                row["input"]["pressure"][-3:] = [0, 0, 0]
                reason = "The last three measured pressure samples are zero."
            key = canonical(row["input"])
            if key in seen:
                continue
            seen.add(key)
            row["evaluation_group"] = variant
            row["target"] = {"label": "sensor_fault", "score": .8, "reason": reason}
            row["provenance"].update(label_method="specified_stress_transformation",
                parent_input_sha256=parent["input_sha256"], independent_support=False)
            row["input_sha256"] = hashlib.sha256(key.encode()).hexdigest()
            row["messages"] = evidence_messages("telemetry", json.dumps(row["input"])) + [
                {"role": "assistant", "content": json.dumps(row["target"], separators=(",", ":"))}]
            result.append(row)
            counts[variant] += 1
            if counts[variant] == policy["cases_per_variant"]:
                break
    return result, counts


def validation_report(path, data, model):
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("split") != "validation" or report.get("weights_sha256") != sha(model):
        raise ValueError("validation report has wrong split or weight identity")
    source = [json.loads(line) for line in (data / "val.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = {r["input_sha256"]: (r["kind"], r["target"]["label"]) for r in source}
    observed = {r["input_sha256"]: (r["task"], r["gold"]) for r in report["rows"]}
    if expected != observed or len(report["rows"]) != len(source):
        raise ValueError("quantization must use the same full fixed new validation set")
    return report, metrics(report["rows"])


def quantization_failures(reference, candidate):
    failures = []
    if reference["macro_f1"]-candidate["macro_f1"] > .02+1e-12:
        failures.append("macro_f1_loss_exceeds_0.02")
    for label in LABELS["proposals"]:
        if candidate["proposals"]["per_class"][label]["recall"] < reference["proposals"]["per_class"][label]["recall"]:
            failures.append(f"proposal_recall_decreased/{label}")
    if candidate["proposals"]["false_positives"] > reference["proposals"]["false_positives"]:
        failures.append("proposal_false_positives_increased")
    if sum(candidate[k]["unavailable"] for k in LABELS) > sum(reference[k]["unavailable"] for k in LABELS):
        failures.append("invalid_responses_increased")
    return failures


def select_quantization(run, data, reports):
    destination = run / "quantization-selection.json"
    if destination.exists() or (run / "freeze.json").exists():
        raise FileExistsError("quantization already selected or candidate frozen")
    results, identities = {}, {}
    for kind, (model, report_path) in reports.items():
        if not model.resolve().is_relative_to((run / "export").resolve()):
            raise ValueError("only this new run's exports can be selected")
        _, results[kind] = validation_report(report_path, data, model)
        identities[kind] = {"model": str(model.resolve()), "model_sha256": sha(model),
                            "report": str(report_path.resolve()), "report_sha256": sha(report_path)}
    failures = {"Q4_K_M": quantization_failures(results["F16"], results["Q4_K_M"])}
    selected = "Q4_K_M"
    if failures["Q4_K_M"]:
        if "Q8_0" not in results:
            raise ValueError("Q4 failed; evaluate Q8 once before selecting a fallback")
        failures["Q8_0"] = quantization_failures(results["F16"], results["Q8_0"])
        selected = "F16" if failures["Q8_0"] else "Q8_0"
    result = {"selected_format": selected, "selected_model": identities[selected]["model"],
              "selected_model_sha256": identities[selected]["model_sha256"], "failures": failures,
              "validation_data_sha256": sha(data / "val.jsonl"), "artifacts": identities,
              "policy_sha256": sha(ROOT / "experiments/slm_quantization_selection.json"),
              "sealed_test_used_for_selection": False, "quality_qualified": False}
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return result


def build_final_data(run, data, recordings):
    frozen = json.loads((run / "freeze.json").read_text(encoding="utf-8"))
    if frozen.get("wire_schema") != VERSION or sha(Path(frozen["model"])) != frozen["model_sha256"]:
        raise ValueError("new candidate must be frozen and intact before opening holdout")
    if frozen.get("stress_policy_sha256") != sha(STRESS_POLICY):
        raise ValueError("stress procedure differs from the frozen procedure")
    path = run / "final-data.jsonl"
    if path.exists() or (run / "final-data-manifest.json").exists():
        raise FileExistsError("final data already built; refusing another holdout build")
    plan_path = recordings / "collection-plan.json"
    if sha(plan_path) != frozen["collection_plan_sha256"]:
        raise ValueError("collection allocation changed after freeze")
    planned, plan_hashes = collection_plans(recordings)
    if frozen.get("collection_plan_hashes") != plan_hashes:
        raise ValueError("collection/supplement allocation changed after freeze")
    # Verify completeness before reading any held-out measurements.
    sealed_specs = [(s, h) for s, h in planned if s["split"] == "sealed"]
    if not sealed_specs:
        raise ValueError("new independent holdout is missing")
    for spec, _ in sealed_specs:
        completed = recordings / "raw/sealed" / f"{spec['recording_id']}.completed.json"
        if not completed.exists():
            raise FileNotFoundError(f"sealed recording incomplete: {spec['recording_id']}")
    seen, old_groups = set(), set()
    for split in ("train", "val"):
        for line in (data / f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            seen.add(canonical(row["input"]))
            old_groups.update(row["provenance"]["source_recordings"])
    rows, rejected, source_hashes = [], Counter(), {}
    for spec, source_plan_hash in sealed_specs:
        if spec["recording_id"] in old_groups:
            raise ValueError("held-out recording crosses into selection data")
        records, metadata = read_recording(recordings, spec, source_plan_hash)
        source_hashes[spec["recording_id"]] = metadata["sha256"]
        for start in range(0, len(records)-30, 32):
            samples = records[start:start+31:2]
            if samples[0]["t"] < 45:
                continue
            row, issue = telemetry_row(samples, spec, metadata["sha256"])
            if row is None:
                rejected[issue] += 1
            else:
                row["evaluation_group"] = "new_native_telemetry"
                rows.append(row)
        if spec["planned_class"] == "normal":
            for start in range(0, len(records)-50, 10):
                samples = records[start:start+51:10]
                if samples[0]["t"] >= 45 and all(r["commands_valid"] and not r["measured"]["scrammed"] for r in samples):
                    for row in proposals(samples, spec, metadata["sha256"]):
                        row["evaluation_group"] = "new_recording_constructed_proposals"
                        rows.append(row)
    conflicts = defaultdict(set)
    for row in rows:
        conflicts[canonical(row["input"])].add((row["kind"], row["target"]["label"]))
    final, groups, counts = [], defaultdict(set), Counter()
    for row in rows:
        key = canonical(row["input"])
        if key in seen or len(conflicts[key]) != 1:
            rejected["duplicate_or_conflicting_identity"] += 1
            continue
        seen.add(key)
        row["input_sha256"] = hashlib.sha256(key.encode()).hexdigest()
        row["messages"] = evidence_messages(row["kind"], json.dumps(row["input"])) + [
            {"role": "assistant", "content": json.dumps(row["target"], separators=(",", ":"))}]
        final.append(row)
        task_class = f"{row['kind']}/{row['target']['label']}"
        counts[task_class] += 1
        groups[task_class].update(row["provenance"]["source_recordings"])
    errors = []
    for kind, labels in LABELS.items():
        for label in labels:
            key = f"{kind}/{label}"
            if counts[key] < 30 or len(groups[key]) < 3:
                errors.append(f"insufficient independent support: {key}, cases={counts[key]}, groups={len(groups[key])}")
    stress_policy = json.loads(STRESS_POLICY.read_text(encoding="utf-8"))
    parents = [r for r in final if r["kind"] == "telemetry" and r["target"]["label"] == "normal"]
    stress, stress_counts = stress_rows(parents, seen, stress_policy)
    final.extend(stress)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(json.dumps(row, allow_nan=False)+"\n" for row in final))
    result = {"n": len(final), "sha256": sha(path), "counts": dict(counts),
              "recording_groups_per_class": {k: sorted(v) for k, v in groups.items()},
              "source_hashes": source_hashes, "quarantine_counts": dict(rejected),
              "support_gate_passed": not errors, "support_errors": errors,
              "stress_counts": stress_counts, "stress_counts_as_independent_support": False,
              "model_frozen_before_opening_test": True, "historical_test_read": False,
              "test_builder_sha256": sha(Path(__file__)), "construction_labels_not_human_gold": True}
    with (run / "final-data-manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return result


def freeze(run, data, recordings, policy_path=POLICY):
    validate_training_readiness(data, policy_path)
    if json.loads((data / "manifest.json").read_text(encoding="utf-8"))["collection_plan_hashes"] != collection_plans(recordings)[1]:
        raise ValueError("final holdout allocation differs from the prepared-data study")
    selection = json.loads((run / "quantization-selection.json").read_text(encoding="utf-8"))
    if selection["validation_data_sha256"] != sha(data / "val.jsonl"):
        raise ValueError("quantization selection data changed")
    config = json.loads((run / "training/config.json").read_text(encoding="utf-8"))
    completed = json.loads((run / "training/completed.json").read_text(encoding="utf-8"))
    if config.get("experiment_policy_sha256") != sha(policy_path) or completed.get("smoke_only") is not False:
        raise ValueError("declared new main training has not completed")
    adapter = Path(json.loads((run / "training/selected.json").read_text(encoding="utf-8"))["checkpoint"])
    tooling = json.loads((run / "export/tooling.json").read_text(encoding="utf-8"))
    if tooling["adapter_sha256"] != sha(adapter / "adapter_model.safetensors"):
        raise ValueError("export does not correspond to selected adapter")
    model = Path(selection["selected_model"])
    if not model.resolve().is_relative_to((run / "export").resolve()) or sha(model) != selection["selected_model_sha256"]:
        raise ValueError("selected deployment export changed")
    identity = {"wire_schema": VERSION, "adapter": str(adapter.resolve()), "adapter_sha256": tooling["adapter_sha256"],
                "model": str(model.resolve()), "model_sha256": sha(model), "context": 3072,
                "generation": {"temperature": 0, "num_predict": 128}, "alias": "scram-observer-evidence-v2",
                "data_manifest_sha256": sha(data / "manifest.json"), "policy_sha256": sha(policy_path),
                "collection_plan_sha256": sha(recordings / "collection-plan.json"),
                "collection_plan_hashes": collection_plans(recordings)[1],
                "stress_policy_sha256": sha(STRESS_POLICY),
                "quantization_selection_sha256": sha(run / "quantization-selection.json"), "release_qualified": False}
    path = run / "freeze.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != identity:
            raise ValueError("frozen new candidate identity changed")
    else:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(identity, stream, indent=2)
    return build_final_data(run, data, recordings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("select-quantization", "freeze"))
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--data", type=Path, default=RUN / "data-v2")
    parser.add_argument("--recordings", type=Path, default=RUN)
    parser.add_argument("--f16-model", type=Path)
    parser.add_argument("--f16-report", type=Path)
    parser.add_argument("--q4-model", type=Path)
    parser.add_argument("--q4-report", type=Path)
    parser.add_argument("--q8-model", type=Path)
    parser.add_argument("--q8-report", type=Path)
    args = parser.parse_args()
    if args.stage == "freeze":
        result = freeze(args.run, args.data, args.recordings)
    else:
        if not all((args.f16_model, args.f16_report, args.q4_model, args.q4_report)):
            parser.error("selection requires F16 and Q4 models/reports")
        reports = {"F16": (args.f16_model, args.f16_report), "Q4_K_M": (args.q4_model, args.q4_report)}
        if args.q8_model or args.q8_report:
            if not args.q8_model or not args.q8_report:
                parser.error("Q8 requires both its model and report")
            reports["Q8_0"] = (args.q8_model, args.q8_report)
        result = select_quantization(args.run, args.data, reports)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
