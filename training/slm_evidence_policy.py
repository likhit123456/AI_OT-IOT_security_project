"""Readiness checks performed before loading a model for evidence-v2 training."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from monitor.observer_runtime import LABELS, parse_verdict
from monitor.telemetry_evidence import VERSION, evidence_messages
from training.shared_slm_data import ROOT, canonical, sha


def validate_training_readiness(data, policy_path):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    if policy["representation"] != VERSION or manifest.get("wire_schema") != VERSION:
        raise ValueError("evidence-v2 policy and dataset representation required")
    if not manifest.get("training_ready") or manifest.get("readiness_errors") or manifest.get("ambiguous_rows_used") != 0:
        raise ValueError("dataset has unresolved support or ambiguous supervision")
    if manifest.get("old_final_test_used") is not False or manifest.get("sealed_data_opened") is not False:
        raise ValueError("selection dataset must exclude old and new final test data")
    expected_code = {"training/slm_evidence_data.py", "monitor/telemetry_evidence.py", "monitor/observer_runtime.py"}
    if set(manifest.get("code_hashes", {})) != expected_code:
        raise ValueError("dataset code provenance is missing")
    if any(sha(ROOT / path) != value for path, value in manifest["code_hashes"].items()):
        raise ValueError("dataset builder/serving representation code changed")
    from training.collect_slm_evidence import collection_plans
    planned, plan_hashes = collection_plans(Path(manifest["recordings_directory"]))
    if manifest.get("collection_plan_hashes") != plan_hashes:
        raise ValueError("collection or supplement allocation changed after data preparation")
    assigned = {s["recording_id"]: (s["split"], h) for s, h in planned}
    if policy["base_revision"] != "7ae557604adf67be50417f59c2c2f167def9a775":
        raise ValueError("unexpected base revision")
    budget = policy["training_budget"]
    if (budget["seed"], budget["optimizer_steps_max"], budget["training_examples_max"],
        budget["label_loss_weight"], budget["epochs_max"]) != (3408, 180, 720, 10, 2):
        raise ValueError("unexpected predeclared training budget")
    seen, groups_by_split = set(), {}
    for split in ("train", "val"):
        path = data / f"{split}.jsonl"
        if sha(path) != manifest["splits"][split]["sha256"]:
            raise ValueError("dataset hash changed")
        counts, groups, all_groups = Counter(), defaultdict(set), set()
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            key = canonical(row["input"])
            if key in seen or hashlib.sha256(key.encode()).hexdigest() != row["input_sha256"]:
                raise ValueError("duplicate or invalid input identity")
            seen.add(key)
            provenance = row["provenance"]
            source_groups = provenance["source_recordings"]
            if provenance.get("ambiguous") is not False or len(source_groups) != 1:
                raise ValueError("ambiguous or imprecise recording provenance")
            group = source_groups[0]
            source = manifest["source_recordings"].get(group, {})
            if source.get("split") != split or source.get("sha256") != provenance["source_sha256"]:
                raise ValueError("source hash/split provenance mismatch")
            if assigned.get(group) != (split, source.get("source_plan_sha256")):
                raise ValueError("source recording differs from its declared collection plan")
            if not source.get("image_id") or not source.get("binary_sha256"):
                raise ValueError("simulator image/binary provenance missing")
            if row["kind"] == "telemetry" and provenance.get("command_provenance") != "recorded controller command, no interlock override":
                raise ValueError("telemetry requires independently recorded commands")
            parse_verdict(json.dumps(row["target"]), row["kind"], "local", False)
            if row["messages"][:2] != evidence_messages(row["kind"], json.dumps(row["input"])):
                raise ValueError("training/serving representation mismatch")
            if len(row["messages"]) != 3 or row["messages"][2]["role"] != "assistant" or json.loads(row["messages"][2]["content"]) != row["target"]:
                raise ValueError("target and assistant supervision disagree")
            task_class = (row["kind"], row["target"]["label"])
            counts[task_class] += 1
            groups[task_class].add(group)
            all_groups.add(group)
        for kind, labels in LABELS.items():
            for label in labels:
                key = (kind, label)
                if counts[key] < (90 if split == "train" else 30) or len(groups[key]) < 3:
                    raise ValueError(f"insufficient independent support: {split}/{kind}/{label}")
        if split == "train" and sum(counts.values()) > budget["training_examples_max"]:
            raise ValueError("training example budget exceeded")
        if all_groups != set(manifest["splits"][split]["recording_ids"]):
            raise ValueError("manifest source group inventory differs")
        groups_by_split[split] = all_groups
    if groups_by_split["train"] & groups_by_split["val"]:
        raise ValueError("source recording crosses train/validation")
    return policy
