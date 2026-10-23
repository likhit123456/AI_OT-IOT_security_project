"""
L2 observer wrapper: the fine-tuned observer model as a live ADVISORY detector
beside the rule-based SequentialMonitor. make_observer() picks what is served:
v3 (Ollama "scram-observer-v3", monitor/observer_v3_format) first, v2
("scram-observer", monitor/observer_v2_format) if v3 is not installed, else
None (rule monitor only).

Each control cycle the engine calls feed(t, state, intent). The wrapper keeps the
two inputs the model was trained on - the brain's last HISTORY_N proposals
(sampled every PROPOSAL_EVERY_S) and the last WINDOW_N measured samples (every
SAMPLE_S) - and runs at most one model call at a time on a background thread, so
the control loop never waits on it. Proposal and telemetry checks alternate.

suspicion() is what Keel may consume, and it is ROUTED by input type: only the
proposal-side labels (provenance_violation, rationale_mismatch, setpoint_drift)
count; the observer's telemetry verdicts are shown but never fed to Keel - the
rule monitors and the SIS own telemetry. normal -> 0; an attack label -> its
score, floored at SCORE_FLOOR. Any bad, invalid or missing output -> 0 and a
note, never a spurious alarm (same degrade-to-safe rule as the operator).

The format module is injectable and brings its own window (WINDOW_N, SAMPLE_S).
A format with TELEMETRY_COMMANDED (v3) also gets, per sample, the valve positions
L1 last sent - the command whose result that sample is, exactly how the training
recordings paired them - and both go through measured_dict's rounding, so a live
window is built the way the training windows were.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Optional

from l3_brain.prompt_format import measured_dict
from monitor import observer_v2_format as V2
from monitor import observer_v3_format as V3

OLLAMA_URL = "http://127.0.0.1:11434"
OBSERVER_MODEL = "scram-observer"
# served in this order, first one installed wins: (format, Ollama model, num_ctx)
CHAIN = ((V3, "scram-observer-v3", 3072),    # v3 telemetry prompts are ~2,450 tokens
         (V2, "scram-observer", None))
SAMPLE_S = 0.5                # sample spacing unless the format sets SAMPLE_S (v2: 6 samples ~2.6 s)
MAX_CONSECUTIVE_ERRORS = 3    # backend errors in a row -> the engine moves down CHAIN
PROPOSAL_EVERY_S = 5.0        # one proposal per operator decision interval
CHECK_EVERY_S = 2.5           # a model call at most this often, alternating input types
SCORE_FLOOR = 0.3             # an attack label never maps below this


@dataclass
class Verdict:
    t: float
    kind: str                 # "proposals" | "telemetry"
    label: Optional[str]      # None = invalid / no output
    score: float
    reason: str
    note: str = "ok"
    latency_s: float = 0.0

    def as_dict(self) -> dict:
        return {"t": round(self.t, 1), "kind": self.kind, "label": self.label, "score": round(self.score, 2),
                "reason": self.reason, "note": self.note, "latency_s": round(self.latency_s, 2)}


def _http_post_json(url: str, body: dict, timeout_s: float) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read())


def observer_available(model: str = OBSERVER_MODEL, base_url: str = OLLAMA_URL, timeout_s: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout_s) as r:
            names = {m.get("name", "") for m in json.loads(r.read()).get("models", [])}
    except Exception:                               # noqa: BLE001
        return False
    return model in names or f"{model}:latest" in names


def make_observer(skip: tuple = (), available: Callable[[str], bool] = observer_available,
                  **kw) -> Optional["Observer"]:
    """The first CHAIN model that is installed (models in `skip` are passed over), or None."""
    for fmt, model, num_ctx in CHAIN:
        if model not in skip and available(model):
            return Observer(fmt=fmt, model=model, num_ctx=num_ctx, **kw)
    return None


class Observer:
    def __init__(self, fmt=V2, model: str = OBSERVER_MODEL, base_url: str = OLLAMA_URL,
                 timeout_s: float = 20.0, background: bool = True, num_ctx: Optional[int] = None,
                 http_post: Callable[[str, dict, float], dict] = _http_post_json):
        self.fmt, self.model, self.base_url = fmt, model, base_url.rstrip("/")
        self.timeout_s, self._background, self._http_post = timeout_s, background, http_post
        self.num_ctx = num_ctx
        self.sample_s = getattr(fmt, "SAMPLE_S", SAMPLE_S)
        self.commanded = getattr(fmt, "TELEMETRY_COMMANDED", False)
        self._lock = threading.Lock()
        self._inflight = False
        self._samples: Deque[tuple] = deque(maxlen=fmt.WINDOW_N)      # (t, measured, commanded)
        self._proposals: Deque[dict] = deque(maxlen=fmt.HISTORY_N)
        self._last_sample_t = self._last_prop_t = self._last_check_t = -1e9
        self._next_kind = "proposals"
        self._proposals_dirty = False
        self.last: dict = {}                    # kind -> Verdict
        self.calls = self.failures = 0
        self.consecutive_errors = 0             # backend errors (not bad outputs) in a row
        self.last_user: dict = {}               # kind -> last user turn sent (for audits)
        self.log: Deque[dict] = deque(maxlen=100)

    # ---- inputs -------------------------------------------------------------
    def reset(self) -> None:
        """Forget history (e.g. after an operator re-arm) and drop old verdicts."""
        with self._lock:
            self._samples.clear()
            self._proposals.clear()
            self.last = {}
            self._last_sample_t = self._last_prop_t = -1e9

    def feed(self, t: float, state, intent=None, commanded=None) -> None:
        """Call once per control cycle; never blocks on the model. commanded: the
        ControlCommand whose result `state` is (L1's last signed command); a
        TELEMETRY_COMMANDED format takes no sample until there is one."""
        if t - self._last_sample_t >= self.sample_s and (commanded is not None or not self.commanded):
            self._last_sample_t = t
            if self.commanded:
                m = measured_dict(state)
                c = {"f1": round(commanded.f1_valve, 4), "f2": round(commanded.f2_valve, 4),
                     "purge": round(commanded.purge_valve, 4), "product": round(commanded.product_valve, 4)}
            else:
                m, c = {k: getattr(state, k) for k in self.fmt.TELEMETRY_FIELDS}, None
                m["scrammed"] = bool(state.scrammed)
            self._samples.append((t, m, c))
        if intent is not None and t - self._last_prop_t >= PROPOSAL_EVERY_S:
            self._last_prop_t = t
            self._proposals.append(self.fmt.proposal_dict(intent, intent.rationale or ""))
            self._proposals_dirty = True
        if t - self._last_check_t >= CHECK_EVERY_S:
            self._maybe_check(t)

    def _maybe_check(self, t: float) -> None:
        kinds = [self._next_kind, "telemetry" if self._next_kind == "proposals" else "proposals"]
        for kind in kinds:
            if kind == "proposals" and (len(self._proposals) < self.fmt.HISTORY_N or not self._proposals_dirty):
                continue
            if kind == "telemetry" and len(self._samples) < self.fmt.WINDOW_N:
                continue
            with self._lock:
                if self._inflight:
                    return
                self._inflight = True
            self._last_check_t = t
            self._next_kind = "telemetry" if kind == "proposals" else "proposals"
            if kind == "proposals":
                self._proposals_dirty = False
                user = self.fmt.proposals_to_json(list(self._proposals))
            else:
                ts = [s[0] for s in self._samples]
                meas = [s[1] for s in self._samples]
                user = (self.fmt.telemetry_to_json(meas, [s[2] for s in self._samples], ts[-1] - ts[0])
                        if self.commanded else self.fmt.telemetry_to_json(meas, ts[-1] - ts[0]))
            self.last_user[kind] = user
            if self._background:
                threading.Thread(target=self._run, args=(t, kind, user), daemon=True).start()
            else:
                self._run(t, kind, user)
            return

    # ---- model call (any failure -> no verdict, suspicion 0) -----------------
    def _run(self, t: float, kind: str, user: str) -> None:
        t0 = time.monotonic()
        options = {"temperature": 0, "num_predict": 120}
        if self.num_ctx:
            options["num_ctx"] = self.num_ctx
        backend_error = False
        try:
            resp = self._http_post(f"{self.base_url}/api/chat", {
                "model": self.model, "stream": False, "format": "json",
                "messages": self.fmt.messages(user), "options": options}, self.timeout_s)
            if not isinstance(resp, dict) or resp.get("error"):
                raise RuntimeError(f"ollama error: {resp.get('error') if isinstance(resp, dict) else resp!r}")
            v, note = self.fmt.parse((resp.get("message") or {}).get("content", ""))
            verdict = (Verdict(t, kind, v["label"], v["score"], v["reason"], "ok") if v
                       else Verdict(t, kind, None, 0.0, "", f"invalid output: {note}"))
        except Exception as e:                      # noqa: BLE001 - never crash the control loop
            backend_error = True
            verdict = Verdict(t, kind, None, 0.0, "", f"error: {type(e).__name__}: {str(e)[:100]}")
        verdict.latency_s = time.monotonic() - t0
        with self._lock:
            self.consecutive_errors = self.consecutive_errors + 1 if backend_error else 0
            self.calls += 1
            self.failures += verdict.label is None
            self.last[kind] = verdict
            self.log.append(verdict.as_dict())
            self._inflight = False

    # ---- output -------------------------------------------------------------
    def suspicion(self) -> float:
        """Advisory suspicion for Keel: proposal-side labels only (see module docstring)."""
        v = self.last.get("proposals")
        if v is None or v.label is None or v.label not in self.fmt.PROPOSAL_LABELS:
            return 0.0
        return max(SCORE_FLOOR, min(1.0, v.score))

    def snapshot(self) -> dict:
        return {"model": self.model, "calls": self.calls, "failures": self.failures,
                "suspicion": round(self.suspicion(), 2),
                "proposals": self.last["proposals"].as_dict() if "proposals" in self.last else None,
                "telemetry": self.last["telemetry"].as_dict() if "telemetry" in self.last else None}
