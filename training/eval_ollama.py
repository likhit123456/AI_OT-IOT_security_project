"""
Test the DEPLOYED operator model (the q4_k_m GGUF served by Ollama) - is it
actually trained?

  1. val      all held-out val states, fine-tuned model vs the stock base model
              (same system prompt + state JSON, temperature 0)
  2. probes   NEW states never in the data: a real nominal val state with
              pressure / level / trip / dropout overridden, incl. combinations
              that never occurred (high P + high L) and one rule with no
              training data (low level). Labels come from the expert policy.

Metrics: valid JSON accepted by LLMOperator's parser, hold accuracy (hold when
the expert holds, act when it acts), per-setpoint mean abs error vs the expert.

Usage: python -m training.eval_ollama [--models scram-operator qwen2.5:1.5b] [--val-n 0]
Results: model/eval_ollama.json
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from l3_brain.policy import parse_llm_targets
from l3_brain.prompt_format import state_to_json, system_prompt
from training.build_dataset import rule_of
from training.expert_policy import expert

REPO = Path(__file__).resolve().parent.parent
VAL = REPO / "training" / "val.jsonl"
OUT = REPO / "model" / "eval_ollama.json"
OLLAMA = "http://127.0.0.1:11434/api/chat"
KEYS = ("pressure_sp", "level_sp", "production_rate_sp", "a_comp_sp")


def ask(model: str, user: str) -> str:
    body = json.dumps({"model": model, "stream": False,
                       "messages": [{"role": "system", "content": system_prompt()},
                                    {"role": "user", "content": user}],
                       "options": {"temperature": 0, "num_predict": 160}}).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["message"]["content"]


def score(model: str, cases: list) -> dict:
    """cases: list of (name, user_json, gold_dict)."""
    n_ok = hold_right = act_right = n_hold = n_act = 0
    errs, per = defaultdict(list), defaultdict(lambda: defaultdict(int))
    rows = []
    t0 = time.time()
    for name, user, gold in cases:
        text = ask(model, user)
        t, note = parse_llm_targets(text)
        try:
            raw = json.loads(text[text.find("{"):text.rfind("}") + 1])
            has_keys = isinstance(raw, dict) and all(k in raw for k in KEYS)
        except ValueError:
            has_keys = False
        ok = t is not None and has_keys
        gold_hold = all(gold[k] is None for k in KEYS)
        n_hold += gold_hold
        n_act += not gold_hold
        per[name]["n"] += 1
        row = {"case": name, "gold": gold, "raw": text[:300], "valid": ok}
        if ok:
            n_ok += 1
            pred = [getattr(t, k) for k in KEYS]
            pred_hold = all(v is None for v in pred)
            right = pred_hold == gold_hold
            hold_right += right and gold_hold
            act_right += right and not gold_hold
            per[name]["decision_right"] += right
            if not gold_hold and not pred_hold:
                for k, v in zip(KEYS, pred):
                    if v is not None and gold[k] is not None:
                        e = abs(v - gold[k])
                        errs[k].append(e)
                        errs[(name, k)].append(e)
            row["pred"] = dict(zip(KEYS, pred))
        rows.append(row)
    mean = lambda v: round(sum(v) / len(v), 2) if v else None
    return {"model": model, "n": len(cases), "valid": n_ok,
            "hold_when_expert_holds": f"{hold_right}/{n_hold}",
            "act_when_expert_acts": f"{act_right}/{n_act}",
            "mae": {k: mean(errs[k]) for k in KEYS},
            "per_case": {c: {**v, "mae": {k: mean(errs[(c, k)]) for k in KEYS}} for c, v in per.items()},
            "secs_per_example": round((time.time() - t0) / max(1, len(cases)), 2), "rows": rows}


def val_cases(limit: int) -> list:
    out = []
    for line in VAL.open():
        m = json.loads(line)["messages"]
        gold = json.loads(m[2]["content"])
        out.append((rule_of(gold["rationale"]), m[1]["content"], gold))
    return out[:limit] if limit else out


def probe_cases() -> list:
    """Real nominal val state, with the safety-relevant fields overridden."""
    base = next(json.loads(json.loads(l)["messages"][1]["content"]) for l in VAL.open()
                if "In band" in json.loads(l)["messages"][2]["content"])
    probes = []

    def add(name, **kw):
        s = {**base, **kw}
        sp, why = expert(s)
        probes.append((name, state_to_json(s), {**sp, "rationale": why}))

    for p in (2500.0, 2700.0, 2790.0, 2830.0, 2875.0, 2920.0, 2945.0, 2980.0):
        add(f"P={p:.0f}", pressure=p)                                  # pressure sweep
    for lv in (60.0, 74.0, 78.0, 83.0, 89.0):
        add(f"L={lv:.0f}", level=lv)                                   # high-level sweep
    for p, lv in ((2900.0, 85.0), (2950.0, 80.0)):
        add(f"P={p:.0f}+L={lv:.0f} (never seen)", pressure=p, level=lv)  # combined, not in data
    add("tripped @ P=2600", scrammed=True, pressure=2600.0)            # tripped even though P is fine
    add("pressure dropout (0)", pressure=0.0)
    add("L=12 (no training data)", level=12.0)                         # untrained rule
    return probes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["scram-operator", "qwen2.5:1.5b"])
    ap.add_argument("--val-n", type=int, default=0, help="limit val examples (0 = all)")
    a = ap.parse_args()
    vals, probes = val_cases(a.val_n), probe_cases()
    res = {}
    for model in a.models:
        print(f"\n=== {model} ===", flush=True)
        r_val, r_probe = score(model, vals), score(model, probes)
        res[model] = {"val": r_val, "probes": r_probe}
        for tag, r in (("val", r_val), ("probes", r_probe)):
            print(f"[{tag}] n={r['n']} valid={r['valid']} hold-right={r['hold_when_expert_holds']} "
                  f"act-right={r['act_when_expert_acts']} mae={r['mae']} ({r['secs_per_example']}s/ex)",
                  flush=True)
        OUT.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
