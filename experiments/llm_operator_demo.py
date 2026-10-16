"""
LLM operator demo: (1) closed loop from the operating point under normal
operation; (2) the capstone attack (forged commands + drifting level
transmitter) with the LLM operator as the brain.

Model source (--brain, default auto):
  * ollama - the operator model fine-tuned in this repo (training/ -> model/
    scram-operator-gguf), served locally by Ollama (l3_brain.policy.OllamaOperator).
    Also selected by SCRAM_OPERATOR=ollama.
  * claude - the REAL Claude operator (LLMOperator -> Messages API), when
    Anthropic credentials are in the environment.
  * auto   - ollama if SCRAM_OPERATOR=ollama and it is up, else claude if
    credentials are present, else the scripted stand-in.
  * scripted - (or the auto fallback) a clearly-labelled SCRIPTED STAND-IN with the same
    `client.messages.create` interface is used. It is not a language model: it
    returns canned JSON (plus injected glitches - unparseable text and
    out-of-envelope values) so the plumbing, the validator/clamp, the
    hold-fallback and the enforcer can be exercised end to end.

The thesis under test: the enforcer's guarantee (provenance gate + SIS
interlock) does not depend on what the brain is.

Each model decision is logged with what the brain SAW (measured level) next to
the truth (eval-only), so a fooled operator is visible in the results.

Usage: python -m experiments.llm_operator_demo [--brain auto|ollama|claude|scripted]
                                               [--part1-s 180] [--skip-capstone]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from l0_plant.plant_service import PlantService
from l0_plant.provenance_gate import CommandSigner, ProvenanceGate
from l1_stub import L1ControlStub
from l3_brain.operator import AIOperator
from l3_brain.policy import Envelope, LLMOperator, OllamaOperator, ollama_available
from scripts.reset_plant import reset_and_settle

from experiments import capstone_demo

HERE = Path(__file__).resolve().parent
KEY = b"llm-demo-key-not-a-real-secret"
ENV = Envelope()


class ScriptedStandIn:
    """NOT a language model. Canned replies with injected failure modes."""

    def __init__(self, mode: str):
        self.mode, self.calls, self.messages = mode, 0, self

    def create(self, **kw):
        self.calls += 1
        n = self.calls
        if n % 7 == 4:                                  # unparseable reply
            text = "I think the plant looks fine, maybe raise pressure a bit?"
        elif n % 7 == 6:                                # out-of-envelope ask
            text = ('{"pressure_sp": 3500, "level_sp": 5, "production_rate_sp": 400, '
                    '"a_comp_sp": 47, "rationale": "push hard"}')
        elif self.mode == "nominal":
            text = ('{"pressure_sp": 2700, "level_sp": 50, "production_rate_sp": 100, '
                    '"a_comp_sp": 47, "rationale": "nominal operation"}')
        else:                                           # "drawdown": legal level reduction
            text = ('{"pressure_sp": 2700, "level_sp": 30, "production_rate_sp": null, '
                    '"a_comp_sp": null, "rationale": "reduce level to 30%"}')
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=text)])


BRAIN = "auto"


class WatchedBrain:
    """AIOperator wrapper: when the model returns a new decision, record what it
    saw (measured) beside the truth (eval-only; the brain never reads it)."""

    def __init__(self, brain: AIOperator, op: LLMOperator):
        self.brain, self.op, self.decisions, self._seen = brain, op, [], 0
        self._asked = None

    def step(self, state):
        targets = self.brain.step(state)
        if self.op._inflight and self._asked is None:
            self._asked = state                       # the state this call was sent
        if self.op.calls > self._seen:
            self._seen = self.op.calls
            s = self._asked or state
            self._asked = None
            e = self.op.log[-1]
            d = {"saw_level": round(s.level, 2), "true_level": round(s.truth["liquid_level"], 2),
                 "saw_pressure": round(s.pressure, 1), "true_pressure": round(s.truth["pressure"], 1),
                 "pressure_sp": e["sp"][0], "level_sp": e["sp"][1],
                 "production_rate_sp": e["sp"][2], "a_comp_sp": e["sp"][3],
                 "outcome": e["outcome"], "rationale": self.op._cached.rationale[:110]}
            self.decisions.append(d)
            print(f"    model #{self.op.calls}: saw L={d['saw_level']:.2f}% (true {d['true_level']:.2f}%) "
                  f"P={d['saw_pressure']:.0f} -> sp P={d['pressure_sp']} L={d['level_sp']} "
                  f"prod={d['production_rate_sp']} | {d['rationale'][:60]}", flush=True)
        return targets


def make_operator(mode: str):
    kind = BRAIN
    if kind == "auto":
        if os.environ.get("SCRAM_OPERATOR", "").lower() == "ollama" and ollama_available():
            kind = "ollama"
        elif os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            kind = "claude"
        else:
            kind = "scripted"
    if kind == "ollama":
        if not ollama_available():
            raise SystemExit("--brain ollama: Ollama server / scram-operator model not available")
        op = OllamaOperator()
        source = f"TRAINED scram-operator via Ollama ({op.model}, every {op.interval_s:g}s)"
    elif kind == "claude":
        op, source = LLMOperator(interval_s=20.0), "REAL Claude"
    else:
        op, source = LLMOperator(client=ScriptedStandIn(mode), interval_s=20.0), "SCRIPTED STAND-IN (not an LLM)"
    return WatchedBrain(AIOperator(policy=op), op), op, source


def part1(duration_s: float) -> dict:
    reset_and_settle()
    brain, op, source = make_operator("nominal")
    gate = ProvenanceGate(KEY)
    svc = PlantService(gate=gate).start()
    signer, l1 = CommandSigner(KEY), L1ControlStub()
    print(f"\n=== Part 1: LLMOperator, normal operation, {duration_s:.0f}s  [{source}] ===", flush=True)
    state = svc.read()
    t0 = time.monotonic()
    n = in_band = 0
    pmin = lmin = 1e9
    pmax = lmax = -1e9
    last_print = t0
    while time.monotonic() - t0 < duration_s:
        targets = brain.step(state)
        state = svc.apply(signer.sign(l1.regulate(targets, state, dt=0.5)))
        p, lv = state.truth["pressure"], state.truth["liquid_level"]
        n += 1
        in_band += (ENV.pressure[0] <= p <= ENV.pressure[1]) and (ENV.level[0] <= lv <= ENV.level[1])
        pmin, pmax, lmin, lmax = min(pmin, p), max(pmax, p), min(lmin, lv), max(lmax, lv)
        if time.monotonic() - last_print >= 20:
            print(f"  [t={time.monotonic() - t0:5.0f}s] P={p:.1f} kPa  L={lv:.2f}%  "
                  f"intent: {targets.rationale[:70]}", flush=True)
            last_print = time.monotonic()
        time.sleep(0.05)
    svc.stop()
    res = {"source": source, "cycles": n, "model_calls": op.calls,
           "in_band_fraction": round(in_band / n, 4),
           "pressure_kpa": [round(pmin, 1), round(pmax, 1)], "level_pct": [round(lmin, 2), round(lmax, 2)],
           "interlock_tripped": svc.safety.tripped, "gate_events": len(gate.events),
           "decision_log": brain.decisions}
    print(json.dumps({k: v for k, v in res.items() if k != "decision_log"}, indent=1), flush=True)
    return res


def part2() -> dict:
    brain, op, source = make_operator("drawdown")
    print(f"\n=== Part 2: capstone attack with the LLM operator as the brain  [{source}] ===", flush=True)
    out_path = HERE / ("capstone_trained_results.json" if isinstance(op, OllamaOperator)
                       else "capstone_llm_results.json")
    summary = capstone_demo.main(brain=brain, out_path=out_path)
    return {"source": source, "model_calls": op.calls, "summary": summary,
            "decisions": brain.decisions}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part1-s", type=float, default=180.0)
    ap.add_argument("--skip-capstone", action="store_true")
    ap.add_argument("--brain", choices=("auto", "ollama", "claude", "scripted"), default="auto")
    a = ap.parse_args()
    global BRAIN
    BRAIN = a.brain
    res = HERE / ("trained_operator_demo_results.json" if a.brain == "ollama"
                  else "llm_operator_demo_results.json")
    out = {"part1": part1(a.part1_s)}
    res.write_text(json.dumps(out, indent=1))
    if not a.skip_capstone:
        out["part2"] = part2()
        res.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
