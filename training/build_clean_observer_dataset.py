"""Build an observer dataset with whole-recording holdouts and exact deduplication.

The original v3 files remain historical evidence for their trained weights.
Run: python -m training.build_clean_observer_dataset
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from l2_monitor import observer_v3_format as F
from training import build_observer_dataset as B

OUT = B.HERE / "observer_clean"
LABELS = ("normal", "excursion", "sensor_fault", "actuator_fault")


def canonical(user: str) -> str:
    return json.dumps(json.loads(user), sort_keys=True, separators=(",", ":"))


def build(out_dir: Path = OUT, seed: int = 19) -> dict:
    rng = random.Random(seed)
    groups = {}
    sources = (("plant", B.load_rollouts(B.STATES)), ("native", B.load_rollouts(B.OBS_STATES)))
    for source, rollouts in sources:
        for name, records in sorted(rollouts.items()):
            pools, bases = B.recorded_telemetry({name: records} if source == "plant" else {},
                                               {name: records} if source == "native" else {})
            heldout = name in (B.UNSEEN_ROLLOUTS if source == "plant" else B.UNSEEN_NATIVE)
            key = "unseen" if heldout else "seen"
            examples = [e for rows in pools[key].values() for e in rows]
            base = bases[key]
            if not examples and not base:
                continue
            labels = {e[0] for e in examples}
            if base:
                labels.update(("sensor_fault", "actuator_fault"))
            groups[f"{source}/{name}"] = {"heldout": heldout, "examples": examples, "base": base, "labels": labels}

    # Select entire source recordings for validation. Synthesized children retain that split.
    available = [g for g in groups if not groups[g]["heldout"]]
    rng.shuffle(available)
    val_groups = set()
    for label in LABELS:
        if any(label in groups[g]["labels"] for g in val_groups):
            continue
        candidates = [g for g in available if g not in val_groups and label in groups[g]["labels"]]
        candidate = next((g for g in candidates if all(
            any(other not in val_groups | {g} and lab in groups[other]["labels"] for other in available)
            for lab in LABELS)), None)
        if candidate is None:
            raise ValueError(f"Need independent source recordings for train/validation class {label}")
        val_groups.add(candidate)

    pools = {s: defaultdict(list) for s in ("train", "val", "unseen")}
    for group, item in groups.items():
        split = "unseen" if item["heldout"] else "val" if group in val_groups else "train"
        examples = list(item["examples"])
        bases = item["base"]
        if bases:
            # Fixed budget per source; do not use validation/test labels to tune generation.
            for _ in range(5000):
                base = rng.choice(bases)
                channels = B.UNSEEN_SENSOR_CH if split == "unseen" else B.TRAIN_SENSOR_CH
                severity = rng.uniform(4, 6) if split == "unseen" else rng.uniform(1, 3)
                example = B.synth_sensor(base, rng.choice(channels), rng.choice(B.SENSOR_MODES), severity, rng)
                if example:
                    examples.append(example)
                valve = rng.choice(B.UNSEEN_VALVES + B.TRAIN_VALVES if split == "unseen" else B.TRAIN_VALVES)
                shape = "offset" if split == "unseen" else rng.choice(("stuck", "gain"))
                example = B.synth_actuator(base, valve, shape, rng)
                if example:
                    examples.append(example)
        for example in examples:
            pools[split][example[0]].append((example, group))

    # Reserve test, then validation, then training. Conflicting labels are excluded everywhere.
    labels_by_user = defaultdict(set)
    for pool in pools.values():
        for rows in pool.values():
            for example, _ in rows:
                labels_by_user[canonical(example[2])].add(example[0])
    conflicts = {u for u, labels in labels_by_user.items() if len(labels) > 1}
    used = set()
    selected = {s: [] for s in pools}
    provenance = {s: set() for s in pools}
    duplicate_counts = Counter()
    shortfalls = {}
    for split in ("unseen", "val", "train"):
        for label in LABELS:
            rows = pools[split][label]
            rng.shuffle(rows)
            want = B.TELEMETRY_COUNTS[split][label]
            for example, group in rows:
                user = canonical(example[2])
                if user in conflicts or user in used:
                    duplicate_counts[split] += 1
                    continue
                used.add(user)
                selected[split].append(example)
                provenance[split].add(group)
                want -= 1
                if want == 0:
                    break
            if want:
                if want == B.TELEMETRY_COUNTS[split][label]:
                    raise ValueError(f"No unique {split}/{label} windows")
                shortfalls[f"{split}/{label}"] = want

    # Keep proposal wording, but deduplicate both within and across splits.
    for split in ("val", "train"):
        for example in B.v2_proposals(split):
            user = canonical(example[2])
            if user not in used:
                used.add(user)
                selected[split].append(example)

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "seed": seed, "strategy": "whole-source-recording",
                "conflicting_users_excluded": len(conflicts), "duplicate_candidates_skipped": dict(duplicate_counts),
                "unique_pool_shortfalls": shortfalls,
                "splits": {}}
    for split, rows in selected.items():
        rng.shuffle(rows)
        content = "".join(json.dumps({"messages": F.messages(e[2], e[3])}) + "\n" for e in rows)
        file = out_dir / f"observer_v3_{split}.jsonl"
        file.write_text(content, encoding="utf-8", newline="\n")
        manifest["splits"][split] = {"n": len(rows), "by_label": dict(Counter(e[0] for e in rows)),
                                    "recordings": sorted(provenance[split]),
                                    "sha256": hashlib.sha256(content.encode()).hexdigest()}
    manifest["cross_split_user_overlap"] = 0
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=OUT)
    parser.add_argument("--seed", type=int, default=19)
    args = parser.parse_args()
    print(json.dumps(build(args.out_dir, args.seed), indent=2))
