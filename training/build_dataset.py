"""
Build the operator fine-tune dataset from REAL plant states.

  states.jsonl (collect_states.py)  --expert_policy-->  chat examples
      system    = operator role + the plant's real design targets and SIS limits
                  + the output JSON contract
      user      = the measured state as JSON (PlantState field names; never truth)
      assistant = setpoints + rationale JSON (the schema LLMOperator parses)

The prompt format (system text + state JSON) comes from l3_brain/prompt_format.py,
the same module LLMOperator serves with; the build asserts the system prompt is
byte-identical to the one served (train/serve match).

Split: exact-duplicate states are dropped, then each rollout is cut into
contiguous 10 s blocks and ~11% of every rollout's blocks go to val, so val
states are not interleaved 0.5 s neighbours of train states. If a rule ends up
with no val example, one train block containing it is moved to val.

Balance: within each split, every minority-rule example is kept and the
"hold design targets" rule is downsampled to at most --cap-design (default 40%)
of the split. Asserts no user-turn overlap between train and val.

Usage: python -m training.build_dataset [--cap-design 0.4] [--seed 0]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from l0_plant.safety import TripLimits
from l3_brain.policy import SYSTEM_PROMPT, Envelope, parse_llm_targets
from l3_brain.prompt_format import state_to_json, system_prompt
from training.expert_policy import expert, load_design_point

HERE = Path(__file__).resolve().parent
STATES = HERE / "states.jsonl"
BLOCK = 20                     # samples per split block (~10 s at 2 Hz)
VAL_FRAC = 0.11


def rule_of(rationale: str) -> str:
    if rationale.startswith("Interlock tripped"):
        return "hold:tripped"
    if rationale.startswith("Pressure or level reading implausible"):
        return "hold:bad_reading"
    if rationale.startswith("In band"):
        return "design_targets"
    tags = []
    if "kPa from trip" in rationale:
        tags.append("pressure_backoff")
    if "low trip" in rationale:
        tags.append("level_low_backoff")
    if "high trip" in rationale:
        tags.append("level_high_backoff")
    return "+".join(tags) or "other"


def histogram(name: str, values, lo: float, hi: float, bins: int, unit: str) -> None:
    w = (hi - lo) / bins
    counts = [0] * bins
    under = over = 0
    for v in values:
        if v < lo:
            under += 1
        elif v >= hi:
            over += 1
        else:
            counts[int((v - lo) / w)] += 1
    peak = max(counts + [under, over, 1])
    print(f"\n{name} ({unit}), n={len(values)}, min={min(values):.2f}, max={max(values):.2f}")
    if under:
        print(f"  {'< ' + format(lo, 'g'):>15} | {'#' * max(1, round(40 * under / peak)):<40} {under}")
    for i, c in enumerate(counts):
        bar = "#" * round(40 * c / peak) if c else ""
        print(f"  {lo + i * w:7.1f}-{lo + (i + 1) * w:<7.1f} | {bar:<40} {c}")
    if over:
        print(f"  {'>= ' + format(hi, 'g'):>15} | {'#' * max(1, round(40 * over / peak)):<40} {over}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap-design", type=float, default=0.40)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    design, lim, env = load_design_point(), TripLimits(), Envelope()
    sys_msg = system_prompt(design, lim)
    assert sys_msg == SYSTEM_PROMPT, ("design_point.json differs from prompt_format.DESIGN_POINT; "
                                      "the served prompt would not match training")

    # load, drop exact duplicate measured states, label each with the expert
    by_rollout, seen, raw_n = defaultdict(list), set(), 0
    with STATES.open() as f:
        for line in f:
            raw_n += 1
            r = json.loads(line)
            user = state_to_json(r["measured"])
            if user in seen:
                continue
            seen.add(user)
            sp, why = expert(r["measured"], design)
            out = json.dumps({**sp, "rationale": why})
            targets, note = parse_llm_targets(out, env)      # must round-trip the LLMOperator parser
            assert targets is not None and note == "ok", (note, out)
            r.update(user=user, assistant=out, rule=rule_of(why))
            by_rollout[r["rollout"]].append(r)

    # block split per rollout
    train_blocks, val_blocks = [], []
    for name, recs in by_rollout.items():
        blocks = [recs[i:i + BLOCK] for i in range(0, len(recs), BLOCK)]
        rng.shuffle(blocks)
        n_val = max(1, round(len(blocks) * VAL_FRAC))
        val_blocks += blocks[:n_val]
        train_blocks += blocks[n_val:]
    # every rule that exists at all gets at least one val block
    for rule in {r["rule"] for b in train_blocks for r in b}:
        if not any(r["rule"] == rule for b in val_blocks for r in b):
            b = next(b for b in train_blocks if any(r["rule"] == rule for r in b))
            train_blocks.remove(b)
            val_blocks.append(b)

    def balance(blocks):
        recs = [r for b in blocks for r in b]
        minority = [r for r in recs if r["rule"] != "design_targets"]
        design_recs = [r for r in recs if r["rule"] == "design_targets"]
        keep = int(len(minority) * a.cap_design / (1.0 - a.cap_design))
        rng.shuffle(design_recs)
        out = minority + design_recs[:keep]
        rng.shuffle(out)
        return out, len(design_recs)

    train, train_design_avail = balance(train_blocks)
    val, val_design_avail = balance(val_blocks)

    for split, recs in (("train", train), ("val", val)):
        with (HERE / f"{split}.jsonl").open("w") as f:
            for r in recs:
                f.write(json.dumps({"messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": r["user"]},
                    {"role": "assistant", "content": r["assistant"]}]}) + "\n")

    users = lambda p: {json.loads(l)["messages"][1]["content"] for l in p.open()}
    overlap = users(HERE / "train.jsonl") & users(HERE / "val.jsonl")
    assert not overlap, f"{len(overlap)} states in both train and val"

    m = [r["measured"] for r in train + val]
    print(f"real states collected: {raw_n} ({raw_n - len(seen)} exact duplicates dropped) "
          f"from {len(by_rollout)} rollouts")
    print(f"train.jsonl: {len(train)}   val.jsonl: {len(val)}   overlap: {len(overlap)}")
    print(f"'hold design targets' downsampled: train {train_design_avail} -> "
          f"{sum(r['rule'] == 'design_targets' for r in train)}, val {val_design_avail} -> "
          f"{sum(r['rule'] == 'design_targets' for r in val)} (cap {a.cap_design:.0%})")
    print("\nper-rollout (train / val):")
    rc = {s: Counter(r["rollout"] for r in recs) for s, recs in (("train", train), ("val", val))}
    for name in by_rollout:
        print(f"  {name:<32} {rc['train'][name]:5d} / {rc['val'][name]:4d}")
    print("\nexpert rule fired (train / val, % of train):")
    rules = {s: Counter(r["rule"] for r in recs) for s, recs in (("train", train), ("val", val))}
    all_rules = ["design_targets", "pressure_backoff", "level_high_backoff", "level_low_backoff",
                 "hold:tripped", "hold:bad_reading"]
    for rule in all_rules + sorted(set(rules["train"]) - set(all_rules)):
        print(f"  {rule:<36} {rules['train'][rule]:5d} / {rules['val'][rule]:4d}   "
              f"{100 * rules['train'][rule] / len(train):5.1f}%")

    print("\n--- coverage of the real measured states (train + val) ---")
    histogram("pressure", [x["pressure"] for x in m], 2300, 3100, 16, "kPa")
    histogram("level", [x["level"] for x in m], 0, 100, 20, "%")
    histogram("product_flow", [x["product_flow"] for x in m], 0, 200, 20, "kmol/h")
    histogram("A in purge", [x["a_in_purge"] * 100 for x in m], 30, 65, 14, "mol %")
    print(f"\ninterlock-tripped states: {sum(x['scrammed'] for x in m)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
