"""Freeze this completed demo before a once-only independent recording test.

Reuses the predeclared evidence-v2 holdout construction, not its main-training
policy. Demo training is a distinct protocol and is never described as satisfying
the original production experiment's training or release gates.
"""
from __future__ import annotations

import json
from pathlib import Path

from training.collect_slm_evidence import collection_plans
from training.freeze_evidence_candidate import STRESS_POLICY, build_final_data
from training.shared_slm_data import sha
from training.train_demo_observer import DESTINATION, SOURCE, assessment


def freeze(run=DESTINATION):
    path = run / "freeze.json"
    if path.exists():
        raise FileExistsError("Demo was already frozen; no re-selection after holdout exposure")
    selected = json.loads((run / "selected.json").read_text())
    completed = json.loads((run / "completed.json").read_text())
    if selected != completed["selected"] or not selected["assessment"]["demo_target_met"]:
        raise ValueError("Demo training is not complete or did not meet its declared target")
    adapter = Path(selected["checkpoint"])
    if sha(adapter / "adapter_model.safetensors") != selected["adapter_sha256"]:
        raise ValueError("Selected adapter changed")
    model = run / "export/scram-observer-slm-f16.gguf"
    report_path = run / "f16-development-validation.json"
    report = json.loads(report_path.read_text())
    if report["weights_sha256"] != sha(model) or not assessment(report["rows"])["demo_target_met"]:
        raise ValueError("F16 export is not the validated demo candidate")
    if report["identity"]["input_sha256"] != sha(run / "validation.jsonl"):
        raise ValueError("Export validation did not use the fixed demo development set")
    tooling = json.loads((run / "export/tooling.json").read_text())
    if tooling["adapter_sha256"] != selected["adapter_sha256"]:
        raise ValueError("Export lineage differs from the selected adapter")
    frozen = {"wire_schema": "measured-evidence-v2", "purpose": "once-only demo independent test",
              "adapter": str(adapter), "adapter_sha256": selected["adapter_sha256"],
              "model": str(model.resolve()), "model_sha256": sha(model), "context": 3072,
              "alias": "scram-observer-demo-f16", "generation": report["generation"],
              "demo_policy_sha256": sha(run / "policy.json"),
              "development_report_sha256": sha(report_path),
              "source_data_manifest_sha256": sha(SOURCE / "data-v2/manifest.json"),
              "collection_plan_sha256": sha(SOURCE / "collection-plan.json"),
              "collection_plan_hashes": collection_plans(SOURCE)[1],
              "stress_policy_sha256": sha(STRESS_POLICY),
              "builder_sha256": sha(Path(__file__)), "release_qualified": False,
              "no_training_or_selection_using_test_results": True}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(frozen, stream, indent=2)
    return build_final_data(run, SOURCE / "data-v2", SOURCE)


if __name__ == "__main__":
    print(json.dumps(freeze(), indent=2))
