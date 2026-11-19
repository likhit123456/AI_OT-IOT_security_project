"""Find conservative source-window candidates for the historical review queue.

Uses only previously assigned train/validation recordings. One changed sensor
channel is allowed for constructed variants; commands can also be constructed.
Matches are evidence for review, not recovered authoritative labels or lineage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

from monitor import observer_v3_format as V3
from monitor.telemetry_evidence import CHANNELS
from training.audit_slm_evidence import DATA, RUN
from training.shared_slm_data import ROOT, canonical, sha


def fingerprints(data):
    """Retain every measured channel except one; omit all commanded channels.

    Both sensor synthesis (one changed reading) and actuator synthesis (changed
    command) preserve these signatures. Never omit actual valve positions.
    """
    measured = {key: value for key, value in data.items() if not key.endswith("_valve_cmd")}
    return [hashlib.sha256(canonical({k: v for k, v in measured.items() if k != ignored}).encode()).hexdigest()
            for ignored in CHANNELS]


def source_index(groups):
    index = defaultdict(set)
    candidates = []
    sources = {"plant": ROOT / "training/states.jsonl", "native": ROOT / "training/observer_states.jsonl"}
    for source, path in sources.items():
        recordings = defaultdict(list)
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                group = f"{source}/{record['rollout']}"
                if group not in groups:
                    continue
                # Discard truth, fault names and phase before indexing.
                recordings[group].append({k: record[k] for k in ("t", "measured", "commanded") if k in record})
        for group, records in sorted(recordings.items()):
            for start in range(len(records)-30):
                window = records[start:start+31:2]
                times = [r["t"] for r in window]
                if any(b <= a for a, b in pairwise(times)):
                    continue
                measured = [r["measured"] for r in window]
                recorded = all("commanded" in r for r in window)
                commands = [r.get("commanded") or {v: r["measured"][f"{v}_valve_pos"] for v in V3.VALVES}
                            for r in window]
                data = json.loads(V3.telemetry_to_json(measured, commands, times[-1]-times[0]))
                identifier = len(candidates)
                candidates.append({"group": group, "start_s": times[0], "end_s": times[-1],
                                   "commands_independently_recorded": recorded, "wire": data})
                for key in fingerprints(data):
                    index[key].add(identifier)
    return index, candidates


def recover(queue, destination, data=DATA):
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    split_groups = {s: set(manifest["splits"][s]["recording_ids"]) for s in ("train", "val")}
    if split_groups["train"] & split_groups["val"]:
        raise ValueError("overlapping recording groups")
    index, candidates = source_index(split_groups["train"] | split_groups["val"])
    destination.mkdir(parents=True, exist_ok=False)
    counts = Counter()
    class_groups = defaultdict(set)
    with (destination / "source-candidates.jsonl").open("w", encoding="utf-8", newline="\n") as out:
        for line in queue.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            matches = set().union(*(index.get(key, set()) for key in fingerprints(row["input"])))
            matches = sorted(i for i in matches if candidates[i]["group"] in split_groups[row["split"]])
            groups = sorted({candidates[i]["group"] for i in matches})
            support = f"{row['split']}/{row['historical_target']['label']}"
            class_groups[support].update(groups if len(groups) == 1 else [])
            counts["reviewed"] += 1
            counts["no_source_match" if not matches else "unique_group" if len(groups) == 1 else "ambiguous_group"] += 1
            command_sources = {candidates[i]["commands_independently_recorded"] for i in matches}
            if command_sources == {False}:
                counts["only_matches_sources_without_recorded_commands"] += 1
            elif command_sources == {True}:
                counts["only_matches_sources_with_recorded_commands"] += 1
            details = []
            for i in matches:
                candidate = candidates[i]
                wire = candidate["wire"]
                details.append({k: v for k, v in candidate.items() if k != "wire"} | {
                    "changed_sensor_channels": [k for k in CHANNELS if row["input"][k] != wire[k]],
                    "commands_match_source_wire": all(row["input"][f"{v}_valve_cmd"] == wire[f"{v}_valve_cmd"]
                                                      for v in V3.VALVES)})
            out.write(json.dumps({"split": row["split"], "input_sha256": row["input_sha256"],
                "candidate_groups": groups, "candidate_windows": details, "lineage_verified": False})+"\n")
    report = {"status": "source_candidates_for_review_only", "counts": dict(counts),
              "class_groups_with_unique_candidate_group": {k: sorted(v) for k, v in sorted(class_groups.items())},
              "source_windows_indexed": len(candidates), "old_final_test_read": False,
              "heldout_source_recordings_indexed": False, "labels_changed": False,
              "method": "Exact rounded-wire match excluding commands and at most one sensor channel; split restricted.",
              "limitations": ["A unique matching source group is not authoritative reconstruction of construction provenance.",
                              "Multiple overlapping windows are correlated, not independent recording support.",
                              "No matching source can indicate transformations affecting several channels or lost provenance."],
              "hashes": {"review_queue": sha(queue), "code": sha(Path(__file__)),
                         "states": sha(ROOT / "training/states.jsonl"),
                         "observer_states": sha(ROOT / "training/observer_states.jsonl"),
                         "source_candidates": sha(destination / "source-candidates.jsonl")}}
    (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=RUN / "supervision-audit-verified/review-queue.jsonl")
    parser.add_argument("--destination", type=Path, default=RUN / "source-recovery")
    args = parser.parse_args()
    print(json.dumps(recover(args.queue, args.destination), indent=2))


if __name__ == "__main__":
    main()
