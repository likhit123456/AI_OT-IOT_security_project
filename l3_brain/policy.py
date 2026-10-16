"""
L3 policy: the decision function the AI operator runs.

The operator (operator.py) owns the loop, logging and the governance handshake;
the *policy* is just the swappable brain: state in, supervisory targets out.
This keeps the seam clean so you can start with a transparent heuristic today
and drop in an RL policy or an LLM-planner later without touching the loop or
the contracts.

A Policy must implement:  decide(state: PlantState) -> SupervisoryTargets
"""

from __future__ import annotations

import json
import math
import sys
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional, Protocol, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import PlantState, SupervisoryTargets   # noqa: E402
from l3_brain.prompt_format import state_to_json, system_prompt   # noqa: E402


class Policy(Protocol):
    def decide(self, state: PlantState) -> SupervisoryTargets: ...


class BaselineOperator:
    """
    A transparent, non-ML baseline so the whole stack runs end-to-end on day
    one and later ML policies have something to beat.

    It nudges the supervisory setpoints toward a safe, economic operating point
    and backs off hard when either (a) the plant is near a protective limit or
    (b) governance has reduced the brain's authority. It expresses intent only
    as setpoints - it never names a valve.
    """

    def __init__(self,
                 pressure_target: float = 2700.0,
                 level_target: float = 50.0,
                 production_target: float = 100.0,
                 a_comp_target: float = 47.0):
        self.pressure_target = pressure_target
        self.level_target = level_target
        self.production_target = production_target
        self.a_comp_target = a_comp_target

    def decide(self, state: PlantState) -> SupervisoryTargets:
        # If L0 has scrammed, intent is irrelevant - ask for nothing and say so.
        if state.scrammed:
            return SupervisoryTargets(
                authority=0.0,
                rationale=f"plant scrammed ({state.scram_reason}); holding",
            )

        # Margin to the high-pressure hazard drives how aggressive we allow the
        # economic target to be. Closer to the limit => lower production ask.
        pressure_margin = (3000.0 - state.pressure)  # kPa to trip
        if pressure_margin < 150.0:
            prod = min(self.production_target, state.product_flow * 0.9)
            rationale = f"pressure margin low ({pressure_margin:.0f} kPa); easing production"
        else:
            prod = self.production_target
            rationale = "nominal economic operation"

        return SupervisoryTargets(
            pressure_sp=self.pressure_target,
            level_sp=self.level_target,
            production_rate_sp=prod,
            a_comp_sp=self.a_comp_target,
            authority=1.0,
            rationale=rationale,
        )


# =============================================================================
# LLM operator (server-side Claude policy)
# =============================================================================
#
# Same Policy.decide(state) -> SupervisoryTargets interface as BaselineOperator,
# so it is drop-in swappable (see make_policy / SCRAM_POLICY). It emits INTENT
# only: the four setpoints plus a short rationale - never valve positions - and
# it reads only PlantState's measured fields, never `.truth` (the independent
# SIS channel belongs to L0's interlock). The model call runs here, in Python,
# with the key taken from the environment; nothing is ever sent to a client.
#
# The model is untrusted by design: its output is parsed, validated and clamped
# to a safe operating envelope, and any failure (timeout, refusal, bad JSON,
# missing key) degrades to "hold" (all setpoints None -> L1 freezes the
# valves). Whatever it asks for, the enforcer underneath (interlock + provenance
# gate) does not depend on it behaving.

@dataclass(frozen=True)
class Envelope:
    """Safe operating envelope for supervisory setpoints (inclusive)."""
    pressure: Tuple[float, float] = (2400.0, 2900.0)     # kPa (SIS trips at 3000)
    level: Tuple[float, float] = (30.0, 70.0)            # %
    production: Tuple[float, float] = (60.0, 180.0)      # kmol/h
    a_comp: Tuple[float, float] = (40.0, 55.0)           # mol %


# Same system prompt and user-turn format the fine-tune dataset is built with
# (l3_brain/prompt_format.py), so the model is served exactly what it was trained on.
SYSTEM_PROMPT = system_prompt()

_SETPOINT_FIELDS = (("pressure_sp", "pressure"), ("level_sp", "level"),
                    ("production_rate_sp", "production"), ("a_comp_sp", "a_comp"))
_RATIONALE_MAX = 200


def hold_targets(why: str) -> SupervisoryTargets:
    """All setpoints None: L1 freezes the valves where they are."""
    return SupervisoryTargets(authority=1.0, rationale=f"hold: {why}")


def parse_llm_targets(text: str, envelope: Optional[Envelope] = None
                      ) -> Tuple[Optional[SupervisoryTargets], str]:
    """
    Parse + validate model output into SupervisoryTargets. Returns
    (targets, note); targets is None on any failure and `note` says why.
    Pure function, no network. Only the four setpoints and the rationale are
    read - any other key (including anything resembling a valve) is ignored.
    """
    env = envelope or Envelope()
    if not isinstance(text, str) or not text.strip():
        return None, "empty model output"
    body = text.strip()
    start, end = body.find("{"), body.rfind("}")      # also tolerates a fenced reply
    if start < 0 or end <= start:
        return None, "no JSON object in model output"
    try:
        obj = json.loads(body[start:end + 1])
    except ValueError as e:
        return None, f"invalid JSON: {e}"
    if not isinstance(obj, dict):
        return None, "model output is not a JSON object"

    values, clamped = {}, []
    for key, env_name in _SETPOINT_FIELDS:
        v = obj.get(key)
        if v is None:
            values[key] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None, f"{key} is not a finite number: {v!r}"
        lo, hi = getattr(env, env_name)
        c = max(lo, min(hi, float(v)))
        if c != float(v):
            clamped.append(f"{key} {float(v):g}->{c:g}")
        values[key] = c

    rationale = obj.get("rationale", "")
    rationale = rationale.strip()[:_RATIONALE_MAX] if isinstance(rationale, str) else ""
    note = ("clamped: " + ", ".join(clamped)) if clamped else "ok"
    if clamped:
        rationale = f"{rationale} [{note}]".strip()
    return SupervisoryTargets(authority=1.0, rationale=rationale or "llm decision", **values), note


def build_state_prompt(state: PlantState) -> str:
    """The user turn: measured fields only, as JSON (prompt_format.state_to_json).
    Never touches state.truth."""
    return state_to_json(state)


class LLMOperator:
    """
    Claude-backed supervisory policy. decide() never blocks on the network and
    never raises: it returns the cached decision immediately, and when the
    supervisory interval has elapsed kicks off ONE background model call whose
    result becomes the new cached decision. L1 keeps regulating every cycle
    against the cached setpoints in between. `background=False` runs the call
    inline (tests).
    """

    def __init__(self, client=None, model: Optional[str] = None,
                 interval_s: float = 20.0, timeout_s: float = 25.0,
                 envelope: Optional[Envelope] = None, max_tokens: int = 1500,
                 clock: Callable[[], float] = time.monotonic, background: bool = True):
        self.model = model or os.environ.get("SCRAM_LLM_MODEL", "claude-opus-5-5")
        self.interval_s, self.timeout_s = interval_s, timeout_s
        self.envelope = envelope or Envelope()
        self.max_tokens = max_tokens
        self._client = client
        self._clock = clock
        self._background = background
        self._lock = threading.Lock()
        self._cached = hold_targets("awaiting first model decision")
        self._last_started: Optional[float] = None
        self._inflight = False
        self.calls = 0
        self.log: Deque[dict] = deque(maxlen=100)     # bounded decision log

    # ---- Policy interface ----------------------------------------------------
    def decide(self, state: PlantState) -> SupervisoryTargets:
        if state.scrammed:
            return SupervisoryTargets(authority=0.0,
                                      rationale=f"plant scrammed ({state.scram_reason}); holding")
        with self._lock:
            now = self._clock()
            due = ((self._last_started is None or now - self._last_started >= self.interval_s)
                   and not self._inflight)
            if due:
                self._inflight, self._last_started = True, now
            cached = self._cached
        if due:
            if self._background:
                threading.Thread(target=self._refresh, args=(state, cached),
                                 daemon=True).start()
            else:
                self._refresh(state, cached)
                with self._lock:
                    cached = self._cached
        return cached

    # ---- model call (any failure -> hold) --------------------------------------
    def _get_client(self):
        if self._client is None:
            import anthropic                      # lazy: offline/tests need no SDK
            self._client = anthropic.Anthropic(timeout=self.timeout_s, max_retries=0)
        return self._client

    def _call_model(self, state: PlantState, last: SupervisoryTargets) -> str:
        resp = self._get_client().messages.create(
            model=self.model, max_tokens=self.max_tokens, system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": build_state_prompt(state)}],
        )
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"model stopped with {resp.stop_reason}")
        return next((b.text for b in resp.content if b.type == "text"), "")

    def _refresh(self, state: PlantState, last: SupervisoryTargets) -> None:
        t0 = self._clock()
        try:
            text = self._call_model(state, last)
            targets, note = parse_llm_targets(text, self.envelope)
            if targets is None:
                targets, outcome = hold_targets(note), f"parse_failed: {note}"
            else:
                outcome = note
        except Exception as e:                    # never crash the control loop
            targets = hold_targets(f"model unavailable ({type(e).__name__})")
            outcome = f"error: {type(e).__name__}: {str(e)[:120]}"
        with self._lock:
            self._cached, self._inflight = targets, False
            self.calls += 1
            self.log.append({"t": round(t0, 2), "outcome": outcome,
                             "sp": [targets.pressure_sp, targets.level_sp,
                                    targets.production_rate_sp, targets.a_comp_sp],
                             # what the model was shown (measured only) and how it answered
                             "saw": {"pressure": round(state.pressure, 1), "level": round(state.level, 2)},
                             "rationale": targets.rationale,
                             "latency_s": round(self._clock() - t0, 2)})


OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "scram-operator"


def _http_post_json(url: str, body: dict, timeout_s: float) -> dict:
    import urllib.request
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read())


class OllamaOperator(LLMOperator):
    """
    The locally fine-tuned operator (model/scram-operator-gguf, served by Ollama)
    as the brain. Same prompt_format (system prompt + measured-state JSON) it was
    trained on, same parse/validate/clamp and hold-on-any-failure as
    LLMOperator - only the model call differs. Local inference is ~1 s, so the
    default supervisory interval is shorter. `http_post(url, body, timeout_s)`
    is injectable (tests mock it; no network).
    """

    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None,
                 interval_s: float = 5.0, timeout_s: float = 30.0,
                 http_post: Callable[[str, dict, float], dict] = _http_post_json, **kw):
        super().__init__(interval_s=interval_s, timeout_s=timeout_s, **kw)
        self.model = model or os.environ.get("SCRAM_OLLAMA_MODEL", OLLAMA_MODEL)
        self.base_url = (base_url or os.environ.get("SCRAM_OLLAMA_URL", OLLAMA_URL)).rstrip("/")
        self._http_post = http_post

    def _call_model(self, state: PlantState, last: SupervisoryTargets) -> str:
        resp = self._http_post(f"{self.base_url}/api/chat", {
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": build_state_prompt(state)}],
            "options": {"temperature": 0, "num_predict": 160}}, self.timeout_s)
        if not isinstance(resp, dict) or resp.get("error"):
            raise RuntimeError(f"ollama error: {resp.get('error') if isinstance(resp, dict) else resp!r}")
        if resp.get("done_reason") == "length":
            raise RuntimeError("model stopped with length")
        msg = resp.get("message")
        return msg.get("content", "") if isinstance(msg, dict) else ""


def ollama_available(model: Optional[str] = None, base_url: Optional[str] = None,
                     timeout_s: float = 2.0) -> bool:
    """True if the Ollama server answers and has the model registered."""
    import urllib.request
    model = model or os.environ.get("SCRAM_OLLAMA_MODEL", OLLAMA_MODEL)
    base_url = (base_url or os.environ.get("SCRAM_OLLAMA_URL", OLLAMA_URL)).rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout_s) as r:
            names = {m.get("name", "") for m in json.loads(r.read()).get("models", [])}
    except Exception:
        return False
    return model in names or f"{model}:latest" in names


def make_policy(kind: Optional[str] = None) -> Policy:
    """
    Select the brain by argument, SCRAM_OPERATOR or SCRAM_POLICY (default
    "baseline"). "ollama" = the locally fine-tuned model; "llm" = Claude. Either
    one, when its backend is unavailable (Ollama down / model not registered, or
    no Anthropic credentials), falls back to the offline BaselineOperator rather
    than a permanent hold.
    """
    kind = (kind or os.environ.get("SCRAM_OPERATOR") or os.environ.get("SCRAM_POLICY")
            or "baseline").strip().lower()
    if kind == "ollama":
        if ollama_available():
            return OllamaOperator()
        print("[policy] SCRAM_OPERATOR=ollama but the Ollama server / model is unavailable; "
              "using BaselineOperator", file=sys.stderr)
    if kind == "llm":
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return LLMOperator()
        print("[policy] SCRAM_POLICY=llm but no Anthropic credentials in the environment; "
              "using BaselineOperator", file=sys.stderr)
    return BaselineOperator()
