"""
L2 observer wrapper: the fine-tuned observer model (Ollama "scram-observer") as a
live ADVISORY detector beside the rule-based SequentialMonitor.

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

The format module is injectable (v2 today: monitor/observer_v2_format; a v3
model brings its own), so swapping models never touches this class.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Optional

from monitor import observer_v2_format as V2

OLLAMA_URL = "http://127.0.0.1:11434"
OBSERVER_MODEL = "scram-observer"
SAMPLE_S = 0.5                # telemetry sample spacing (v2 windows: 6 samples ~2.6 s)
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


class Observer:
    def __init__(self, fmt=V2, model: str = OBSERVER_MODEL, base_url: str = OLLAMA_URL,
                 timeout_s: float = 20.0, background: bool = True,
                 http_post: Callable[[str, dict, float], dict] = _http_post_json):
        self.fmt, self.model, self.base_url = fmt, model, base_url.rstrip("/")
        self.timeout_s, self._background, self._http_post = timeout_s, background, http_post
        self._lock = threading.Lock()
        self._inflight = False
        self._samples: Deque[tuple] = deque(maxlen=fmt.WINDOW_N)      # (t, measured dict)
        self._proposals: Deque[dict] = deque(maxlen=fmt.HISTORY_N)
        self._last_sample_t = self._last_prop_t = self._last_check_t = -1e9
        self._next_kind = "proposals"
        self._proposals_dirty = False
        self.last: dict = {}                    # kind -> Verdict
        self.calls = self.failures = 0
        self.log: Deque[dict] = deque(maxlen=100)

    # ---- inputs -------------------------------------------------------------
    def reset(self) -> None:
        """Forget history (e.g. after an operator re-arm) and drop old verdicts."""
        with self._lock:
            self._samples.clear()
            self._proposals.clear()
            self.last = {}
            self._last_sample_t = self._last_prop_t = -1e9

    def feed(self, t: float, state, intent=None) -> None:
        """Call once per control cycle; never blocks on the model."""
        if t - self._last_sample_t >= SAMPLE_S:
            self._last_sample_t = t
            m = {k: getattr(state, k) for k in self.fmt.TELEMETRY_FIELDS}
            m["scrammed"] = bool(state.scrammed)
            self._samples.append((t, m))
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
                user = self.fmt.telemetry_to_json([s[1] for s in self._samples], ts[-1] - ts[0])
            if self._background:
                threading.Thread(target=self._run, args=(t, kind, user), daemon=True).start()
            else:
                self._run(t, kind, user)
            return

    # ---- model call (any failure -> no verdict, suspicion 0) -----------------
    def _run(self, t: float, kind: str, user: str) -> None:
        t0 = time.monotonic()
        try:
            resp = self._http_post(f"{self.base_url}/api/chat", {
                "model": self.model, "stream": False, "format": "json",
                "messages": self.fmt.messages(user),
                "options": {"temperature": 0, "num_predict": 120}}, self.timeout_s)
            if not isinstance(resp, dict) or resp.get("error"):
                raise RuntimeError(f"ollama error: {resp.get('error') if isinstance(resp, dict) else resp!r}")
            v, note = self.fmt.parse((resp.get("message") or {}).get("content", ""))
            verdict = (Verdict(t, kind, v["label"], v["score"], v["reason"], "ok") if v
                       else Verdict(t, kind, None, 0.0, "", f"invalid output: {note}"))
        except Exception as e:                      # noqa: BLE001 - never crash the control loop
            verdict = Verdict(t, kind, None, 0.0, "", f"error: {type(e).__name__}: {str(e)[:100]}")
        verdict.latency_s = time.monotonic() - t0
        with self._lock:
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
