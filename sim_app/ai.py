"""
AI operator for the clean sim. Two capabilities, no interlock or enforcer:

1. parse_command(text): the STOCK qwen2.5:1.5b (Ollama) turns plain English into
   one JSON action {action, valve, amount}, constrained by a JSON schema. A
   small grammar check then settles "to N" (absolute) vs "by N" (relative),
   which a 1.5B model mixes up; any correction is reported, never hidden.

2. Rescue: the TRAINED scram-operator (Ollama, via l3_brain.policy.OllamaOperator
   - same prompt format, parse, validate and envelope clamp it was trained for)
   runs closed-loop every ~2 s. It reads the measured state and emits setpoints;
   `regulate()` turns them into valve positions; the loop applies them and
   stops once pressure and level are back near the design point.

regulate() is deliberately simple and directionally correct for this TE model:
    pressure above setpoint -> open purge, cut both feeds, open product a little
    at setpoint             -> purge at its design opening (vents feed 1's inert B)
    pressure below setpoint -> purge shut, feeds raised
    production setpoint     -> both feeds scaled from the design lineup
    level above setpoint    -> open product (below -> close it)
It never drives feeds from product flow (the l1_stub's production loop does,
and fights the level loop; that is the feed-cut bug it avoids).
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from collections import deque
from typing import Callable, Optional

from contracts import PlantState, SupervisoryTargets
from l3_brain.policy import OllamaOperator
from l3_brain.prompt_format import DESIGN_POINT
from scripts.reset_plant import DESIGN_PURGE_VALVE_PCT

OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"
PARSER_MODEL = "qwen2.5:1.5b"
VALVES = ("f1", "f2", "purge", "product")
# The simulator's own steady state. The purge is the corrected design opening: the
# simulator itself starts it at ~0 (an out-of-bounds read; see scripts/reset_plant.py),
# which lets inert B from feed 1 accumulate and pressure creep.
DESIGN_VALVES = {"f1": 31.45, "f2": 13.24, "purge": DESIGN_PURGE_VALVE_PCT, "product": 47.03}

# ---------------------------------------------------------------- 1. commands
PARSER_SYSTEM = """You convert an operator's plain-English command for a chemical reactor into ONE JSON action.
Valves: f1 = feed 1, f2 = feed 2, purge = purge valve (vent), product = product valve (liquid outlet). Positions are percent open, 0-100.
Actions:
- "adjust": change a valve BY an amount. amount is signed: positive opens/increases/raises, negative closes/decreases/cuts/lowers/reduces.
- "set": set a valve TO an absolute position (words like "to", "at", "fully open"=100, "shut"/"close fully"=0).
- "rescue": the operator wants the AI to stabilise / save / recover the reactor or bring it back to normal.
- "stop": the operator wants the AI to stop, hand control back, or wants manual control.
- "unknown": anything else.
Reply with JSON only."""
PARSER_EXAMPLES = [
    ("increase feed 1 by 5%", {"action": "adjust", "valve": "f1", "amount": 5}),
    ("cut purge to 0", {"action": "set", "valve": "purge", "amount": 0}),
    ("close the product valve by 3 percent", {"action": "adjust", "valve": "product", "amount": -3}),
    ("open purge to 25%", {"action": "set", "valve": "purge", "amount": 25}),
    ("get the plant back to normal", {"action": "rescue", "valve": None, "amount": None}),
    ("take manual control", {"action": "stop", "valve": None, "amount": None}),
]
PARSER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["adjust", "set", "rescue", "stop", "unknown"]},
        "valve": {"type": ["string", "null"], "enum": ["f1", "f2", "purge", "product", None]},
        "amount": {"type": ["number", "null"]},
    },
    "required": ["action", "valve", "amount"],
}
_NUM = r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct)?"
_ABS = re.compile(r"\b(?:to|at)\s+" + _NUM, re.I)
_REL = re.compile(r"\bby\s+" + _NUM, re.I)
_DOWN = re.compile(r"\b(close|closing|decrease|reduce|lower|cut|drop|shut|less|down)\b", re.I)
_UP = re.compile(r"\b(open|opening|increase|raise|more|up|add|bump|boost)\b", re.I)


_ANY_NUM = re.compile(r"\d")
_VALVE_NAMES = re.compile(r"\b(feed ?[12]|f[12])\b", re.I)
_SHUT = re.compile(r"\b(shut|close|closed|closing)\b", re.I)
_FULL_OPEN = re.compile(r"\b(fully open|open fully|wide open|open all the way|full open)\b", re.I)


def _direction(text: str) -> int:
    """+1 / -1 if the sentence only uses opening / closing words, 0 if it uses both or
    neither (e.g. "open the purge to lower the pressure": the goal is not the valve)."""
    up, down = bool(_UP.search(text)), bool(_DOWN.search(text))
    return 1 if up and not down else -1 if down and not up else 0


def _post(url: str, body: dict, timeout_s: float) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read())


def _check(text: str, action: str, valve, amount) -> tuple:
    """Validate one parsed action and settle what a 1.5B model mixes up, using the words of
    its own clause: "to/at N" = set, "by N" = adjust, and the sign of an adjust when the
    clause only uses opening or only closing words. Returns (action, valve, amount, corrections)."""
    fixes = []
    if action not in ("adjust", "set", "rescue", "stop", "unknown"):
        action = "unknown"
    if action not in ("adjust", "set"):
        return action, None, None, fixes
    if valve not in VALVES or not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return "unknown", valve, amount, fixes
    amount = float(amount)
    if not _ANY_NUM.search(_VALVE_NAMES.sub(" ", text)):   # "feed 2" is a name, not an amount
        target = 100.0 if _FULL_OPEN.search(text) else 0.0 if _SHUT.search(text) else None
        if target is not None:
            if (action, amount) != ("set", target):
                fixes.append(f"model said {action} {amount:+g}; '{text.strip()}' means set {target:g}")
            return "set", valve, target, fixes
    m_abs, m_rel = _ABS.search(text), _REL.search(text)
    if m_abs and not m_rel and (action != "set" or amount != float(m_abs.group(1))):
        fixes.append(f"model said {action} {amount:+g}; text says "
                     f"'{m_abs.group(0).strip()}' -> set {float(m_abs.group(1)):g}")
        action, amount = "set", float(m_abs.group(1))
    elif m_rel and not m_abs and action == "set":
        n = float(m_rel.group(1)) * (-1 if _direction(text) < 0 else 1)
        fixes.append(f"model said set {amount:g}; text says '{m_rel.group(0).strip()}' -> adjust {n:+g}")
        action, amount = "adjust", n
    elif action == "adjust" and amount * _direction(text) < 0:
        word = (_DOWN if _direction(text) < 0 else _UP).search(text).group(0)
        fixes.append(f"model said {amount:+g}; '{word}' means "
                     f"{'close' if amount > 0 else 'open'} -> {-amount:+g}")
        amount = -amount
    return action, valve, amount, fixes


def _ask(post, system: str, examples: list, text: str, schema: dict) -> tuple:
    msgs = [{"role": "system", "content": system}]
    for q, a in examples:
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": json.dumps(a)}]
    msgs.append({"role": "user", "content": text})
    t0 = time.monotonic()
    resp = post(OLLAMA_CHAT, {"model": PARSER_MODEL, "stream": False, "messages": msgs,
                              "format": schema, "options": {"temperature": 0}}, 60.0)
    return (resp.get("message") or {}).get("content", ""), round(time.monotonic() - t0, 2)


def parse_command(text: str, post: Callable[[str, dict, float], dict] = _post) -> dict:
    """Plain English -> ONE action {action, valve, amount, model_output, corrections, latency_s}."""
    raw, latency = _ask(post, PARSER_SYSTEM, PARSER_EXAMPLES, text, PARSER_SCHEMA)
    out = {"model_output": raw, "latency_s": latency}
    try:
        obj = json.loads(raw)
    except ValueError:
        return {**out, "corrections": [], "action": "unknown", "valve": None, "amount": None}
    action, valve, amount, fixes = _check(text, obj.get("action"), obj.get("valve"), obj.get("amount"))
    return {**out, "corrections": fixes, "action": action, "valve": valve, "amount": amount}


# ---- several actions in one command ------------------------------------------------
MAX_ACTIONS = 8
MULTI_SYSTEM = """You convert an operator's plain-English command for a chemical reactor into a list of JSON actions, one per instruction, in the order given.
Valves: f1 = feed 1, f2 = feed 2, purge = purge valve (vent), product = product valve (liquid outlet). Positions are percent open, 0-100.
Each action has:
- "clause": the exact words of the command this action comes from.
- "action":
  - "adjust": change a valve BY an amount. amount is signed: positive opens/increases/raises, negative closes/decreases/cuts/lowers/reduces.
  - "set": set a valve TO an absolute position (words like "to", "at", "fully open"=100, "shut"/"close fully"=0).
  - "rescue": the operator wants the AI to stabilise / save / recover the reactor or bring it back to normal.
  - "stop": the operator wants the AI to stop, hand control back, or wants manual control.
  - "unknown": anything else.
- "valve" and "amount" (null for rescue / stop / unknown).
When one instruction names several valves ("open feed 1 and feed 2 to 100%"), give one action per valve.
Reply with JSON only."""
MULTI_EXAMPLES = [
    ("increase feed 1 by 5%", {"actions": [
        {"clause": "increase feed 1 by 5%", "action": "adjust", "valve": "f1", "amount": 5}]}),
    ("cut purge to 0 and open product by 10%", {"actions": [
        {"clause": "cut purge to 0", "action": "set", "valve": "purge", "amount": 0},
        {"clause": "open product by 10%", "action": "adjust", "valve": "product", "amount": 10}]}),
    ("open feed 1 and feed 2 to 100%", {"actions": [
        {"clause": "open feed 1 and feed 2 to 100%", "action": "set", "valve": "f1", "amount": 100},
        {"clause": "open feed 1 and feed 2 to 100%", "action": "set", "valve": "f2", "amount": 100}]}),
    ("close the product valve by 3 percent, then save the reactor", {"actions": [
        {"clause": "close the product valve by 3 percent", "action": "adjust", "valve": "product", "amount": -3},
        {"clause": "save the reactor", "action": "rescue", "valve": None, "amount": None}]}),
    ("take manual control", {"actions": [
        {"clause": "take manual control", "action": "stop", "valve": None, "amount": None}]}),
]
MULTI_SCHEMA = {
    "type": "object",
    "properties": {"actions": {"type": "array", "minItems": 1, "maxItems": MAX_ACTIONS, "items": {
        "type": "object",
        "properties": {
            "clause": {"type": "string"},
            "action": {"type": "string", "enum": ["adjust", "set", "rescue", "stop", "unknown"]},
            "valve": {"type": ["string", "null"], "enum": ["f1", "f2", "purge", "product", None]},
            "amount": {"type": ["number", "null"]},
        },
        "required": ["clause", "action", "valve", "amount"]}}},
    "required": ["actions"],
}
_SPLIT = re.compile(r"\s*(?:[,;]|\band then\b|\bthen\b|\band\b)\s*", re.I)
_VALVE_WORDS = {"f1": r"\b(feed ?1|feed one|f1)\b", "f2": r"\b(feed ?2|feed two|f2)\b",
                "purge": r"\bpurge\b", "product": r"\bproduct\b"}


def _clause_for(text: str, clause, valve, n_actions: int) -> str:
    """The words to grammar-check an action against: the model's clause if it is really
    part of the command, else the command segment that names this valve; with a single
    action, the whole command."""
    if isinstance(clause, str) and clause.strip() and clause.strip().lower() in text.lower():
        return clause.strip()
    if n_actions == 1:
        return text
    if valve in _VALVE_WORDS:
        segs = [g for g in _SPLIT.split(text) if g and re.search(_VALVE_WORDS[valve], g, re.I)]
        if len(segs) == 1:
            return segs[0]
    return ""                                           # unsure: leave the model's reading alone


def parse_commands(text: str, post: Callable[[str, dict, float], dict] = _post) -> dict:
    """Plain English -> several actions, in order:
    {actions: [{clause, action, valve, amount, corrections}], model_output, latency_s}."""
    raw, latency = _ask(post, MULTI_SYSTEM, MULTI_EXAMPLES, text, MULTI_SCHEMA)
    out = {"model_output": raw, "latency_s": latency, "actions": []}
    try:
        items = json.loads(raw).get("actions")
    except (ValueError, AttributeError):
        items = None
    if not isinstance(items, list) or not items:
        out["actions"] = [{"clause": text, "action": "unknown", "valve": None, "amount": None, "corrections": []}]
        return out
    for it in items[:MAX_ACTIONS]:
        it = it if isinstance(it, dict) else {}
        words = _clause_for(text, it.get("clause"), it.get("valve"), len(items))
        action, valve, amount, fixes = _check(words, it.get("action"), it.get("valve"), it.get("amount"))
        out["actions"].append({"clause": it.get("clause") if isinstance(it.get("clause"), str) else "",
                               "action": action, "valve": valve, "amount": amount, "corrections": fixes,
                               "_words": words})
    # A clause such as "open feed 1 and feed 2 to 100%" can come back with an action for
    # only one of its valves. Give every valve the clause names, and that no action
    # covers, the same action - and say so.
    covered = {a["valve"] for a in out["actions"] if a["action"] in ("adjust", "set")}
    for a in list(out["actions"]):
        if a["action"] not in ("adjust", "set") or not a["_words"] or len(out["actions"]) >= MAX_ACTIONS:
            continue
        for v, pattern in _VALVE_WORDS.items():
            if v not in covered and re.search(pattern, a["_words"], re.I):
                covered.add(v)
                out["actions"].insert(out["actions"].index(a) + 1, {
                    "clause": a["clause"], "action": a["action"], "valve": v, "amount": a["amount"],
                    "corrections": [f"model skipped {v} in '{a['_words']}'; applied the same {a['action']} "
                                    f"{a['amount']:g} to it"], "_words": a["_words"]})
    for a in out["actions"]:
        a.pop("_words", None)
    return out


# ------------------------------------------------------------------ 2. rescue
KP_PURGE = 0.5          # % purge per kPa above the pressure setpoint (200 kPa over -> fully open)
KP_PRODUCT_P = 0.05     # % extra product per kPa above setpoint (vent liquid too)
FEED_CUT_KPA = 400.0    # feeds fall linearly to 25% of their scheduled value 300 kPa over setpoint
KL_PRODUCT = 4.0        # % product per % level above setpoint
FEED_BOOST_KPA = 80.0   # below setpoint: +1x feed per 80 kPa of deficit, so feeds stay near full
FEED_BOOST_MAX = 2.2    # until the last ~80 kPa, then taper (3.2x ~ feed 1 fully open)
TICK_S = 2.0
NEAR_P_KPA, NEAR_L_PCT, NEAR_TICKS = 30.0, 2.0, 3
MAX_RESCUE_S = 600.0
MAX_IO_FAILS = 5        # consecutive plant read/write failures before the AI disengages (~10 s)
MAX_MODEL_FAILS = 3     # consecutive model failures (no decision) before the AI disengages


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def regulate(t: SupervisoryTargets, state: PlantState) -> Optional[dict]:
    """Setpoints -> valve positions (absolute %). None = the model holds."""
    if all(v is None for v in (t.pressure_sp, t.level_sp, t.production_rate_sp, t.a_comp_sp)):
        return None
    p_sp = t.pressure_sp if t.pressure_sp is not None else DESIGN_POINT["pressure"]
    l_sp = t.level_sp if t.level_sp is not None else DESIGN_POINT["level"]
    prod = t.production_rate_sp if t.production_rate_sp is not None else DESIGN_POINT["production"]
    over = state.pressure - p_sp
    scale = prod / DESIGN_POINT["production"]
    if over > 0:
        scale *= max(0.25, 1.0 - over / FEED_CUT_KPA)
    else:
        scale *= 1.0 + min(FEED_BOOST_MAX, -over / FEED_BOOST_KPA)
    return {
        "f1": _clamp(DESIGN_VALVES["f1"] * scale),
        "f2": _clamp(DESIGN_VALVES["f2"] * scale),
        "purge": _clamp(DESIGN_VALVES["purge"] + KP_PURGE * over),   # design purge vents feed-1 inerts
        "product": _clamp(DESIGN_VALVES["product"] + KL_PRODUCT * (state.level - l_sp)
                          + KP_PRODUCT_P * max(0.0, over)),
    }


def plant_state(raw: dict) -> PlantState:
    m, s, o = raw.get("measured", {}), raw.get("state", {}), raw.get("outputs", {})
    return PlantState(
        pressure=m.get("pressure", 0.0), level=m.get("liquid_level", 0.0),
        f1_flow=m.get("f1_flow", 0.0), f2_flow=m.get("f2_flow", 0.0),
        purge_flow=m.get("purge_flow", 0.0), product_flow=m.get("product_flow", 0.0),
        a_in_purge=m.get("A_in_purge", 0.0), b_in_purge=m.get("B_in_purge", 0.0),
        c_in_purge=m.get("C_in_purge", 0.0),
        f1_valve_pos=s.get("f1_valve_pos", 0.0), f2_valve_pos=s.get("f2_valve_pos", 0.0),
        purge_valve_pos=s.get("purge_valve_pos", 0.0), product_valve_pos=s.get("product_valve_pos", 0.0),
        cost=o.get("cost", 0.0))


class Activity:
    """Bounded feed of what the AI did, read by the page."""

    def __init__(self):
        self._lock, self._id, self.items = threading.Lock(), 0, deque(maxlen=300)

    def add(self, kind: str, text: str, **detail) -> None:
        with self._lock:
            self._id += 1
            self.items.append({"id": self._id, "ts": time.time(), "kind": kind, "text": text, **detail})

    def since(self, i: int) -> list:
        with self._lock:
            return [x for x in self.items if x["id"] > i]


class Rescue:
    """Closed loop: trained model -> regulate() -> valves, every TICK_S.

    Each engagement gets its own stop event and run id, so a quick Stop -> Save cannot
    leave an old loop running beside the new one, and an old loop can never change the
    state of a newer one when it exits."""

    def __init__(self, plant, activity: Activity):
        self.plant, self.activity = plant, activity
        self.active = False
        self._lock = threading.Lock()
        self._run_id = 0
        self._stop_evt: Optional[threading.Event] = None

    def start(self) -> None:
        with self._lock:
            if self.active:
                return
            self._run_id += 1
            run_id, stop = self._run_id, threading.Event()
            self._stop_evt, self.active = stop, True
        threading.Thread(target=self._run, args=(run_id, stop), name=f"ai-rescue-{run_id}",
                         daemon=True).start()

    def stop(self, why: str = "operator took manual control") -> None:
        with self._lock:
            if not self.active:
                return
            self._stop_evt.set()
            self.active = False
        self.activity.add("info", f"AI disengaged: {why}. Manual control.")

    def _finish(self, run_id: int, kind: str, text: str) -> None:
        """End this run, unless it was already stopped or superseded by a newer run."""
        with self._lock:
            if not (self.active and self._run_id == run_id):
                return
            self.active = False
        self.activity.add(kind, text)

    def _run(self, run_id: int, stop: threading.Event) -> None:
        op = OllamaOperator(interval_s=0.0, background=False)
        self.activity.add("info", f"AI engaged: trained {op.model} is flying the plant "
                                  f"(decision every {TICK_S:g}s). Goal: back to "
                                  f"{DESIGN_POINT['pressure']:g} kPa / {DESIGN_POINT['level']:g}%.")
        t0, near, io_fails, model_fails, first = time.monotonic(), 0, 0, 0, True
        end = ("info", "AI disengaged. Manual control.")
        try:
            while not stop.is_set():
                tick = time.monotonic()
                if self.plant.ruptured:
                    end = ("error", "Reactor ruptured: nothing left to save. AI disengaged.")
                    break
                if self.plant.resetting:
                    end = ("info", "Simulation is resetting. AI disengaged.")
                    break
                try:
                    if first or self.plant.raw.get("state", {}).get("e_stop"):
                        self.plant.write({"e_stop": 0})        # the AI cannot fly an e-stopped plant
                        first = False
                    state = plant_state(self.plant.read())
                except (OSError, ValueError, RuntimeError) as e:
                    io_fails += 1
                    if io_fails >= MAX_IO_FAILS:
                        end = ("error", f"Plant unreachable for {io_fails} attempts ({type(e).__name__}). "
                                        "AI disengaged: take manual control.")
                        break
                    self.activity.add("error", f"Plant read failed ({type(e).__name__}); retrying "
                                               f"({io_fails}/{MAX_IO_FAILS}).")
                    stop.wait(TICK_S)
                    continue
                io_fails = 0
                targets = op.decide(state)                     # model call (parse, validate, clamp)
                if stop.is_set():                              # stopped while the model was thinking
                    break
                entry = op.log[-1] if op.log else {}
                outcome = str(entry.get("outcome", ""))
                model_fails = model_fails + 1 if outcome.startswith(("error", "parse_failed")) else 0
                if model_fails >= MAX_MODEL_FAILS:
                    end = ("error", f"scram-operator gave no usable decision {model_fails} times in a row "
                                    f"({outcome[:80]}). AI disengaged: take manual control.")
                    break
                valves = regulate(targets, state)
                before = {v: round(getattr(state, f"{v}_valve_pos"), 1) for v in VALVES}
                if valves is not None:
                    try:
                        self.plant.apply_valves(valves, source="ai")
                    except Exception as e:                  # noqa: BLE001 - ruptured / resetting / socket
                        if not (self.plant.ruptured or self.plant.resetting):
                            io_fails += 1
                            self.activity.add("error", f"Valve write failed ({type(e).__name__}); retrying.")
                            stop.wait(TICK_S)
                        continue                             # ruptured / resetting handled at loop top
                self.activity.add(
                    "decision",
                    targets.rationale,
                    saw={"pressure": round(state.pressure, 1), "level": round(state.level, 2)},
                    setpoints={"pressure": targets.pressure_sp, "level": targets.level_sp,
                               "production": targets.production_rate_sp, "a_comp": targets.a_comp_sp},
                    valves=None if valves is None else {v: [before[v], round(valves[v], 1)] for v in VALVES},
                    outcome=outcome, latency_s=entry.get("latency_s"))
                dp = abs(state.pressure - DESIGN_POINT["pressure"])
                dl = abs(state.level - DESIGN_POINT["level"])
                near = near + 1 if (dp <= NEAR_P_KPA and dl <= NEAR_L_PCT) else 0
                if near >= NEAR_TICKS:
                    if stop.is_set():
                        break
                    self.plant.apply_valves(dict(DESIGN_VALVES), source="ai")
                    end = ("done", f"Reactor stabilised: {state.pressure:.0f} kPa, "
                                   f"{state.level:.1f}% (design {DESIGN_POINT['pressure']:g} kPa / "
                                   f"{DESIGN_POINT['level']:g}%) after {time.monotonic() - t0:.0f}s. "
                                   "Valves left at the design lineup. Manual control.")
                    break
                if time.monotonic() - t0 > MAX_RESCUE_S:
                    end = ("error", f"Not back near the design point after {MAX_RESCUE_S:.0f}s. "
                                    "AI disengaged: take manual control.")
                    break
                stop.wait(max(0.0, TICK_S - (time.monotonic() - tick)))
        except Exception as e:                              # noqa: BLE001
            end = ("error", f"AI loop error: {type(e).__name__}: {e}. AI disengaged: take manual control.")
        finally:
            self._finish(run_id, *end)
