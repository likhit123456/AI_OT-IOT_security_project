"""
Sequential (cumulative) monitor -- the spec's "Lookout" (L2), advisory only.

BehaviorMonitor (monitor/behavior_monitor.py) judges ONE step at a time: a
setpoint jump over a cap, a rate over a cap, a frozen signal. An attacker who
keeps every step under those caps is invisible to it by construction (the
"sub-threshold / distributed" attack). This monitor looks at the TRAJECTORY of
what L3 asks for:

  1. CUSUM per setpoint channel  - accumulates same-direction creep that no
     single step reveals (e.g. 20 kPa steps under a 100 kPa step cap).
  2. Same-sign run length        - n consecutive moves in one direction.
  3. Rationale screen            - weak lexical check of the agent's free-text
     rationale for injection-style language (the indirect-injection surface).
  4. Keel corroboration          - if the trusted enforcer reports divergence /
     rate-bound / load-line, suspicion is floored up (L2 reads Keel *events*,
     never the SIS channel itself).

Output is a score in [0,1] + a short reason, i.e. the spec's SuspicionSignal.
It is ADVISORY: feed it to Keel.step(suspicion=...). It can only lower
authority there, with a floor, so a defeated or hostile L2 cannot cause an
unsafe actuation and cannot zero authority on its own.

Like BehaviorMonitor it reads only measured fields + requested targets.
Known limit (state it in the viva): a patient attacker can hold CUSUM below
threshold too (Distributed Detectability Band). That is why Keel, not this
class, is the gate.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from contracts import PlantState, SupervisoryTargets

# (k = allowance per change, h = alarm threshold, eps = min move to count as a change)
DEFAULT_CHANNELS: Dict[str, Tuple[float, float, float]] = {
    "pressure_sp":        (5.0, 150.0, 0.5),
    "level_sp":           (0.5, 10.0, 0.05),
    "production_rate_sp": (2.0, 40.0, 0.2),
    "a_comp_sp":          (0.5, 10.0, 0.05),
}

# weak, deliberately small; tune/replace with a classifier. weights sum, capped at 1.
INJECTION_PATTERNS: Tuple[Tuple[str, float], ...] = (
    (r"\bignore (all |any )?(previous|prior|above)\b", 0.6),
    (r"\b(disable|bypass|override|turn off)\b.{0,30}\b(interlock|scram|safety|alarm|sis|guard)", 0.7),
    (r"\b(maintenance|test|debug|service) mode\b", 0.3),
    (r"\b(advisory|notice|bulletin)\b.{0,40}\b(raise|increase|ignore|set)\b", 0.3),
    (r"\bas (the )?(admin|administrator|supervisor|engineer)\b", 0.3),
    (r"\bsystem prompt\b", 0.4),
)


@dataclass(frozen=True)
class Suspicion:
    t: float
    score: float
    reason: str
    source: str            # cusum | run | rationale | keel | none

    def as_dict(self) -> dict:
        return {"t": self.t, "score": round(self.score, 3), "reason": self.reason, "source": self.source}


@dataclass
class _Chan:
    k: float
    h: float
    eps: float
    last: Optional[float] = None
    s_pos: float = 0.0
    s_neg: float = 0.0
    run_sign: int = 0
    run_len: float = 0.0     # decays between moves, so fractional


class SequentialMonitor:
    def __init__(self, channels: Optional[Dict[str, Tuple[float, float, float]]] = None,
                 leak: float = 0.97, run_alarm: int = 8, min_run: int = 3,
                 decay_half_life_s: float = 120.0):
        self.leak, self.run_alarm, self.min_run = leak, run_alarm, min_run
        # Evidence fades with elapsed time every cycle, moving or not, so a burst of
        # legitimate same-direction moves doesn't peg suspicion forever. Time-based
        # (not per call) so it doesn't depend on the loop rate.
        self.decay_half_life_s = decay_half_life_s
        self._last_t: Optional[float] = None
        self.ch = {n: _Chan(*p) for n, p in (channels or DEFAULT_CHANNELS).items()}
        self.history: Deque[Suspicion] = deque(maxlen=2000)
        self._pat = [(re.compile(p, re.I), w) for p, w in INJECTION_PATTERNS]

    def observe(self, state: PlantState, targets: SupervisoryTargets, t: float,
                keel_codes: Iterable[str] = ()) -> Suspicion:
        """One cycle. `state` is accepted for interface symmetry / future
        measured-only detectors; the current detectors use the targets."""
        cands: List[Tuple[float, str, str]] = [(0.0, "none", "")]

        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        fade = 0.5 ** (dt / self.decay_half_life_s)
        for c in self.ch.values():
            c.s_pos *= fade
            c.s_neg *= fade
            c.run_len *= fade

        for name, c in self.ch.items():
            v = getattr(targets, name, None)
            if v is None:
                continue
            if c.last is None:
                c.last = v
                continue
            d = v - c.last
            if abs(d) < c.eps:
                continue                       # same setpoint re-issued: not a new move
            c.last = v
            # a move first undoes opposite-direction evidence it is reversing, so a
            # back-off and return scores its net displacement, not twice its size;
            # a one-way staircase has nothing to undo and scores exactly as before
            up = max(0.0, d - c.s_neg * self.leak)
            down = max(0.0, -d - c.s_pos * self.leak)
            c.s_pos = max(0.0, c.s_pos * self.leak + (up if d > 0 else d) - c.k)
            c.s_neg = max(0.0, c.s_neg * self.leak + (down if d < 0 else -d) - c.k)
            sign = 1 if d > 0 else -1
            c.run_len = c.run_len + 1 if sign == c.run_sign else 1
            c.run_sign = sign

        for name, c in self.ch.items():
            s = max(c.s_pos, c.s_neg)
            if s > 0:
                cands.append((min(1.0, s / c.h), "cusum",
                              f"{name}: {'upward' if c.s_pos >= c.s_neg else 'downward'} creep "
                              f"(CUSUM {s:.1f}/{c.h:.0f})"))
            if c.run_len >= self.min_run:
                cands.append((min(1.0, (c.run_len - self.min_run + 1) / (self.run_alarm - self.min_run + 1)),
                              "run", f"{name}: {c.run_len:.0f} consecutive same-direction moves"))

        rs, why = self._rationale(targets.rationale)
        if rs > 0:
            cands.append((rs, "rationale", why))

        codes = set(keel_codes)
        if codes & {"rate_bound", "load_line"}:
            cands.append((0.8, "keel", "enforcer reports physical trend violation"))
        elif "divergence" in codes:
            cands.append((0.6, "keel", "enforcer reports measured/SIS divergence"))

        score, source, reason = max(cands, key=lambda x: x[0])
        sig = Suspicion(t, score, reason, source)
        self.history.append(sig)
        return sig

    def reset_channel(self, name: str) -> None:
        """Call after a human-approved setpoint change so legitimate intent doesn't linger."""
        c = self.ch[name]
        c.s_pos = c.s_neg = 0.0
        c.run_len, c.run_sign = 0, 0

    def reset_all(self, baseline: Optional[Dict[str, float]] = None) -> None:
        """Clear every channel's CUSUM and run state (e.g. after an operator re-arm).
        Also forget the last setpoint: with `baseline` (e.g. the design setpoints)
        the next setpoint is measured from it, otherwise it becomes a fresh
        baseline rather than a 'move' from the pre-trip value."""
        for name, c in self.ch.items():
            self.reset_channel(name)
            c.last = (baseline or {}).get(name)

    def _rationale(self, text: str) -> Tuple[float, str]:
        hits = [(w, p.pattern) for p, w in self._pat if text and p.search(text)]
        if not hits:
            return 0.0, ""
        return min(1.0, sum(w for w, _ in hits)), f"rationale matches {len(hits)} injection pattern(s)"
