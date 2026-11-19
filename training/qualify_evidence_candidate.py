"""Compare actual new final reports with unchanged specialists; never promote.

Unmeasured runtime gates remain blockers, even when static quality passes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from monitor.observer_runtime import LABELS
from training.collect_slm_evidence import RUN
from training.shared_slm_data import ROOT, sha
from training.shared_slm_native import metrics


def qualify(run):
    output = run / "qualification.json"
    if output.exists():
        raise FileExistsError(output)
    frozen = json.loads((run / "freeze.json").read_text(encoding="utf-8"))
    manifest = json.loads((run / "final-data-manifest.json").read_text(encoding="utf-8"))
    candidate_path, legacy_path = run / "new-final-candidate.json", run / "new-final-legacy.json"
    candidate, legacy = [json.loads(path.read_text(encoding="utf-8")) for path in (candidate_path, legacy_path)]
    if candidate.get("split") != "final" or legacy.get("split") != "final":
        raise ValueError("qualification requires the independent final reports")
    if candidate.get("weights_sha256") != frozen["model_sha256"]:
        raise ValueError("candidate report weight identity differs from freeze")
    for report in (candidate, legacy):
        if report["identity"]["input_sha256"] != manifest["sha256"]:
            raise ValueError("report does not use the frozen independent input set")
    def identity(rows):
        return {(r["input_sha256"], r["task"], r["gold"]) for r in rows}
    if (identity(candidate["rows"]) != identity(legacy["rows"]) or
        len(candidate["rows"]) != len(legacy["rows"]) or len(identity(candidate["rows"])) != len(candidate["rows"])):
        raise ValueError("paired final case identities differ or contain duplicates")
    if sha(run / "final-data.jsonl") != manifest["sha256"]:
        raise ValueError("independent final data changed")
    gold = [json.loads(line) for line in (run / "final-data.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = {(r["input_sha256"], r["kind"], r["target"]["label"]) for r in gold}
    if not gold or identity(candidate["rows"]) != expected or len(candidate["rows"]) != manifest["n"]:
        raise ValueError("paired reports must contain the entire frozen independent case set")
    original = json.loads((ROOT / "artifacts/optimization/weight-hashes.json").read_text(encoding="utf-8"))
    for item in original[:2]:
        if item["sha256"] not in legacy["weights"].values() or sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("specialist baseline was not the unchanged original")
    faults, comparisons = [], {}
    def primary(report):
        return [r for r in report["rows"] if r["evaluation_group"] not in ("frozen_level", "pressure_dropout")]
    c, b = metrics(primary(candidate)), metrics(primary(legacy))
    if not manifest.get("support_gate_passed"):
        faults.extend(manifest.get("support_errors", ["independent_class_support_unverified"]))
    for kind, labels in LABELS.items():
        for label in labels:
            for metric in ("recall", "precision"):
                current = c[kind].get("per_class", {}).get(label, {}).get(metric)
                baseline = b[kind].get("per_class", {}).get(label, {}).get(metric)
                key = f"{kind}/{label}/{metric}"
                passed = current is not None and baseline is not None and current >= baseline
                comparisons[key] = {"candidate": current, "legacy": baseline, "passed": passed}
                if not passed:
                    faults.append(key)
    if c["proposals"]["false_positives"] > b["proposals"]["false_positives"]:
        faults.append("proposal_false_positives_regressed")
    for name, report in (("candidate", candidate), ("legacy", legacy)):
        telemetry = [r for r in primary(report) if r["task"] == "telemetry"]
        if any("combined" not in row for row in telemetry):
            faults.append(f"{name}_combined_telemetry_evidence_missing")
    def combined_misses(report):
        return sum(r.get("combined") in (None, "normal") for r in primary(report)
                   if r["task"] == "telemetry" and r["gold"] != "normal")
    if combined_misses(candidate) > combined_misses(legacy):
        faults.append("combined_telemetry_missed_faults_regressed")
    invalid_rate = sum(r["candidate"] is None for r in candidate["rows"])/len(candidate["rows"])
    if invalid_rate > .01:
        faults.append("invalid_output_rate_exceeds_0.01")
    for group in ("frozen_level", "pressure_dropout"):
        candidates = [r for r in candidate["rows"] if r["evaluation_group"] == group]
        baselines = [r for r in legacy["rows"] if r["evaluation_group"] == group]
        def recall(rows):
            return sum(r["candidate"] == "sensor_fault" for r in rows)/len(rows) if rows else None
        cr, br = recall(candidates), recall(baselines)
        passed = len(candidates) >= 20 and len(baselines) == len(candidates) and cr >= br
        comparisons[group] = {"candidate_recall": cr, "legacy_recall": br, "n": len(candidates), "passed": passed}
        if not passed:
            faults.append(f"{group}_stress_failed_or_insufficient")
    runtime = json.loads((ROOT / "experiments/unified_observer_gates.json").read_text(encoding="utf-8"))["runtime"]
    result = {"quality_passed": not faults, "quality_failures": faults, "comparisons": comparisons,
              "candidate_primary_metrics": c, "legacy_primary_metrics": b,
              "candidate_invalid_output_rate": invalid_rate, "runtime_gate_requirements": runtime,
              "runtime_gates_measured": False, "release_qualified": False, "defaults_changed": False,
              "status": "quality_failed" if faults else "runtime_qualification_required",
              "proposal_limitations": "Constructed language templates do not establish independent operator-language coverage.",
              "hashes": {"candidate_report": sha(candidate_path), "legacy_report": sha(legacy_path),
                         "freeze": sha(run / "freeze.json"), "code": sha(__file__)}}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RUN)
    args = parser.parse_args()
    result = qualify(args.run)
    print(json.dumps({"status": result["status"], "quality_failures": result["quality_failures"]}, indent=2))


if __name__ == "__main__":
    main()
