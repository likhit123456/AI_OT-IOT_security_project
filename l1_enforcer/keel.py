"""
Keel -- the deterministic enforcer (spec layer "L1").

Sits on the actuation path between L1 control (PI loops) and L0 PlantService:

    L3 targets -> L1 regulate() -> ControlCommand -> Keel.step() -> signer -> L0

What it adds on top of what L0 already has (l0_plant/safety.py is a binary,
last-resort trip; provenance_gate.py checks WHO sent a command):

  1. Rate-of-rise bound + time-to-trip projection on the independent SIS
     channel: acts BEFORE the hard trip, gradually, instead of e_stop-or-nothing.
  2. Load Line: cumulative budget over a sliding window (net pressure climb /
     net level loss). Catches the slow "every step is legal" attack that a
     per-step rate bound cannot see.
  3. Divergence check: |measured - SIS| per channel. A spoofed or drifting
     basic-control sensor shows up as disagreement between the two views.
  4. Authority budget A in [0,1]: decays on evidence, recovers slowly when
     clean. Scales how far any valve may move per cycle. Exposed via
     `authority()` so it plugs straight into AIOperator(authority_fn=...).
  5. Safe fallback: on a hazard, blend the command toward a cause-specific safe
     valve lineup in proportion to severity (closest safe action, not a slam).
  6. Hash-chained, HMAC-signed event log (tamper-evident).

Design rules (these are the security argument -- keep them true):
  * Deterministic. No ML, no network, no randomness, no wall clock (time is
    passed in), so every run is replayable.
  * Hazard decisions use ONLY the SIS channel (`sis`). The measured view is
    used for the divergence check only; it can never relax a clamp.
  * L2 suspicion is advisory: it can only LOWER authority, and its total
    influence is floored (`l2_floor`), so a compromised/hostile L2 cannot
    drive the plant to zero authority on its own (no DoS-by-monitor).
  * The Keel never raises a command's magnitude; it only moves it toward safe.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Deque, Dict, List, Optional, Tuple

from shared.contracts import ControlCommand, PlantState
from l0_plant.safety import TripLimits

VALVES = ("f1_valve", "f2_valve", "purge_valve", "product_valve")


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class KeelLimits:
    trip: TripLimits = field(default_factory=TripLimits)   # must match L0's SIS limits
    # soft zone: Keel starts clamping this far inside the SIS trip point
    pressure_margin_kpa: float = 150.0
    level_margin_pct: float = 3.0           # soft zone starts at trip.level_low + this (whatever limit is configured)
    # rate bound on the SIS channel (kPa/s, %/s), measured over rate_window_s
    pressure_rate_max: float = 2.0
    level_rate_max: float = 0.01
    rate_window_s: float = 5.0
    rate_min_span_s: float = 4.0
    horizon_s: float = 30.0                  # clamp if projected to hit trip within this
    # Load Line: cumulative budget over window_s
    window_s: float = 120.0
    pressure_rise_budget_kpa: float = 150.0
    level_window_s: float = 300.0
    level_drop_budget_pct: float = 3.0
    # divergence (measured vs SIS) tolerances, must exceed normal sensor noise
    pressure_div_kpa: float = 50.0
    level_div_pct: float = 4.0
    div_cycles: int = 5                      # consecutive cycles before it counts
    # authority budget
    slew_max_pct_per_s: float = 5.0          # valve move per second at A = 1
    loss_per_s: Tuple[Tuple[str, float], ...] = (
        ("rate_bound", 0.02), ("load_line", 0.02), ("divergence", 0.01),
        ("soft_zone", 0.03), ("l2_suspicion", 0.01),
    )
    gate_reject_loss: float = 0.20           # impulse per provenance rejection
    recover_per_s: float = 0.01
    recover_after_clean_s: float = 30.0
    l2_floor: float = 0.5                    # L2 alone can never push A below this
    divert_below: float = 0.15
    divert_hold_s: float = 20.0


@dataclass(frozen=True)
class SisFrame:
    """The independent safety-channel reading (PlantState.truth). Trusted-only."""
    pressure: float
    level: float

    @staticmethod
    def from_state(state: PlantState) -> "SisFrame":
        tr = state.truth or {}
        return SisFrame(pressure=float(tr.get("pressure", math.nan)),
                        level=float(tr.get("liquid_level", math.nan)))

    def valid(self) -> bool:
        return math.isfinite(self.pressure) and math.isfinite(self.level)


@dataclass(frozen=True)
class Reason:
    code: str          # rate_bound | load_line | divergence | soft_zone | sis_invalid
    hazard: str        # pressure_high | level_low | level_high | sensor
    severity: float    # 0..1, drives the blend toward the safe lineup
    detail: str


@dataclass(frozen=True)
class Decision:
    verdict: str                     # allow | clamp | block | divert
    command: ControlCommand          # what to hand to the signer / L0
    authority: float
    severity: float
    reasons: Tuple[Reason, ...]


# --------------------------------------------------------------------------- signed log
class EventLog:
    """Append-only, hash-chained, HMAC-tagged. verify() detects edit/delete/reorder."""

    def __init__(self, key: bytes, max_len: int = 5000):
        self._key, self._prev, self._n = key, "0" * 64, 0
        self.events: Deque[dict] = deque(maxlen=max_len)

    def append(self, t: float, kind: str, detail: dict) -> dict:
        self._n += 1
        body = {"i": self._n, "t": round(t, 3), "kind": kind, "detail": detail, "prev": self._prev}
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
        mac = hmac.new(self._key, raw.encode(), hashlib.sha256).hexdigest()
        self._prev = mac
        ev = {**body, "mac": mac}
        self.events.append(ev)
        return ev

    @staticmethod
    def verify(key: bytes, events: List[dict]) -> bool:
        prev = events[0]["prev"] if events else "0" * 64
        for ev in events:
            body = {k: ev[k] for k in ("i", "t", "kind", "detail", "prev")}
            if body["prev"] != prev:
                return False
            raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
            if not hmac.compare_digest(ev["mac"], hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()):
                return False
            prev = ev["mac"]
        return True


# --------------------------------------------------------------------------- the Keel
def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at/below lo, 1 at/above hi, linear between."""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


class Keel:
    def __init__(self, log_key: bytes, limits: Optional[KeelLimits] = None):
        self.lim = limits or KeelLimits()
        self.log = EventLog(log_key)
        self._p: Deque[Tuple[float, float]] = deque()      # (t, SIS pressure)
        self._l: Deque[Tuple[float, float]] = deque()      # (t, SIS level)
        self._div_n = {"pressure": 0, "level": 0}
        self._A = 1.0
        self._clean_since: Optional[float] = None
        self._last_t: Optional[float] = None
        self._last_applied: Optional[ControlCommand] = None
        self._low_since: Optional[float] = None
        self.divert = False
        self.last: Optional[Decision] = None

    # public ---------------------------------------------------------------
    def authority(self) -> float:
        """Plug into AIOperator(authority_fn=keel.authority)."""
        return self._A

    def note_gate_rejection(self, t: float, detail: str = "") -> None:
        """Call when ProvenanceGate rejects a command (impulse authority loss)."""
        self._A = max(0.0, self._A - self.lim.gate_reject_loss)
        self._clean_since = None
        self.log.append(t, "gate_rejection", {"detail": detail, "authority": round(self._A, 3)})

    def rearm(self, state: PlantState) -> None:
        """Operator re-armed the SIS (a deliberate, trusted action): start again from
        the plant's current valves with full authority. If a real hazard is still
        present the next step() re-clamps, so this never skips a check."""
        self._last_applied = ControlCommand(f1_valve=state.f1_valve_pos, f2_valve=state.f2_valve_pos,
                                            purge_valve=state.purge_valve_pos,
                                            product_valve=state.product_valve_pos)
        self._A = 1.0
        self._clean_since = self._low_since = None
        self.divert = False
        self._p.clear()
        self._l.clear()
        self._div_n = {"pressure": 0, "level": 0}
        self.last = None                   # stale pre-trip reasons would feed L2 suspicion
        self.log.append(self._last_t or 0.0, "rearm", {"authority": 1.0})

    def step(self, cmd: ControlCommand, state: PlantState, sis: SisFrame, t: float,
             suspicion: float = 0.0) -> Decision:
        dt = 0.0 if self._last_t is None else max(0.0, min(t - self._last_t, 5.0))
        self._last_t = t

        reasons = self._evaluate(state, sis, t)
        self._update_authority(reasons, suspicion, dt, t)
        # a reading already past the trip point gives severity > 1; the blend is only defined on [0, 1]
        severity = max(0.0, min(1.0, max((r.severity for r in reasons), default=0.0)))

        applied, exempt = cmd, ()
        if reasons and severity > 0.0:
            hazard = max(reasons, key=lambda r: r.severity).hazard
            applied = self._toward_safe(applied, hazard, severity)
            exempt = tuple(self.safe_lineup(hazard, cmd))      # never throttle safety moves
        applied = self._slew_limit(applied, dt, exempt)

        changed = any(abs(getattr(applied, v) - getattr(cmd, v)) > 1e-6 for v in VALVES)
        self._update_divert(t)
        verdict = "divert" if self.divert else ("clamp" if changed else "allow")
        # never let an out-of-range valve value leave Keel (non-finite is left for L0 to refuse)
        applied = applied.clamped() if applied.is_finite() else applied
        d = Decision(verdict, replace(applied, seq=0, auth_tag=""), self._A, severity, tuple(reasons))
        self._last_applied = d.command
        if verdict != "allow" or reasons:
            self.log.append(t, "decision", {
                "verdict": verdict, "authority": round(self._A, 3), "severity": round(severity, 3),
                "reasons": [f"{r.code}:{r.hazard}:{r.detail}" for r in reasons]})
        self.last = d
        return d

    # checks -----------------------------------------------------------------
    def _evaluate(self, state: PlantState, sis: SisFrame, t: float) -> List[Reason]:
        lim, tr = self.lim, self.lim.trip
        out: List[Reason] = []
        if not sis.valid():
            return [Reason("sis_invalid", "pressure_high", 1.0, f"SIS reading invalid {sis}")]
        self._push(self._p, t, sis.pressure, max(lim.window_s, lim.rate_window_s))
        self._push(self._l, t, sis.level, max(lim.level_window_s, lim.rate_window_s))

        # soft zone (inside the SIS trip point)
        s = _ramp(sis.pressure, tr.pressure_high - lim.pressure_margin_kpa, tr.pressure_high)
        if s > 0:
            out.append(Reason("soft_zone", "pressure_high", s, f"P={sis.pressure:.0f}"))
        s = _ramp(-sis.level, -(tr.level_low + lim.level_margin_pct), -tr.level_low)
        if s > 0:
            out.append(Reason("soft_zone", "level_low", s, f"L={sis.level:.1f}"))
        s = _ramp(sis.level, tr.level_high - lim.level_margin_pct, tr.level_high)
        if s > 0:
            out.append(Reason("soft_zone", "level_high", s, f"L={sis.level:.1f}"))

        # rate bound + time-to-trip projection
        rp = self._rate(self._p, t)
        if rp is not None and rp > lim.pressure_rate_max:
            ttt = (tr.pressure_high - sis.pressure) / rp
            out.append(Reason("rate_bound", "pressure_high",
                              max(0.3, 1.0 - ttt / lim.horizon_s) if ttt < lim.horizon_s else 0.0,
                              f"dP/dt={rp:.2f} kPa/s, ttt={ttt:.0f}s"))
        rl = self._rate(self._l, t)
        if rl is not None and rl < -lim.level_rate_max:
            ttt = (sis.level - tr.level_low) / -rl
            out.append(Reason("rate_bound", "level_low",
                              max(0.3, 1.0 - ttt / lim.horizon_s) if ttt < lim.horizon_s else 0.0,
                              f"dL/dt={rl:.3f} %/s, ttt={ttt:.0f}s"))

        # Load Line: net climb / net loss inside the window
        rise = sis.pressure - min(v for ts, v in self._p if t - ts <= lim.window_s)
        if rise > lim.pressure_rise_budget_kpa:
            out.append(Reason("load_line", "pressure_high",
                              0.4 + 0.6 * _ramp(rise, lim.pressure_rise_budget_kpa,
                                                2 * lim.pressure_rise_budget_kpa),
                              f"+{rise:.0f} kPa in {lim.window_s:.0f}s (budget {lim.pressure_rise_budget_kpa:.0f})"))
        drop = max(v for ts, v in self._l if t - ts <= lim.level_window_s) - sis.level
        if drop > lim.level_drop_budget_pct:
            out.append(Reason("load_line", "level_low",
                              0.4 + 0.6 * _ramp(drop, lim.level_drop_budget_pct,
                                                2 * lim.level_drop_budget_pct),
                              f"-{drop:.1f}% in {lim.level_window_s:.0f}s (budget {lim.level_drop_budget_pct:.1f})"))

        # divergence: measured vs independent channel (evidence only, severity 0:
        # it costs authority but never relaxes or tightens the physical clamp)
        for name, m, a, tol in (("pressure", state.pressure, sis.pressure, lim.pressure_div_kpa),
                                ("level", state.level, sis.level, lim.level_div_pct)):
            bad = (not math.isfinite(m)) or abs(m - a) > tol
            self._div_n[name] = self._div_n[name] + 1 if bad else 0
            if self._div_n[name] >= lim.div_cycles:
                out.append(Reason("divergence", "sensor", 0.0,
                                  f"{name}: measured {m:.1f} vs SIS {a:.1f}"))
        return out

    # authority ---------------------------------------------------------------
    def _update_authority(self, reasons: List[Reason], suspicion: float, dt: float, t: float) -> None:
        lim = self.lim
        rates = dict(lim.loss_per_s)
        loss = sum(rates.get(c, 0.0) for c in {r.code for r in reasons if r.code in rates})
        if loss > 0:
            self._A = max(0.0, self._A - loss * dt)
            self._clean_since = None
        # L2 is advisory: it can only lower A, and never below l2_floor on its own.
        # It doesn't reset the clean timer either, so a persistent (maybe false) L2
        # score caps A near l2_floor but can't block recovery up to it.
        l2_loss = rates["l2_suspicion"] * max(0.0, min(1.0, suspicion)) * dt
        if l2_loss > 0:
            self._A = max(self._A - l2_loss, min(self._A, lim.l2_floor))
        if loss == 0:
            if self._clean_since is None:
                self._clean_since = t
            if t - self._clean_since >= lim.recover_after_clean_s:
                self._A = min(1.0, self._A + lim.recover_per_s * dt)

    def _update_divert(self, t: float) -> None:
        if self._A <= self.lim.divert_below:
            self._low_since = t if self._low_since is None else self._low_since
            if t - self._low_since >= self.lim.divert_hold_s:
                self.divert = True
        else:
            self._low_since = None
            self.divert = False

    # actuation ---------------------------------------------------------------
    @staticmethod
    def safe_lineup(hazard: str, cmd: ControlCommand) -> Dict[str, float]:
        """Cause-specific safe valves, mirroring SafetyInterlock.safe_state_inputs()."""
        if hazard == "level_low":
            return {"product_valve": 0.0}
        if hazard == "level_high":
            return {"f1_valve": 0.0, "f2_valve": 0.0, "product_valve": 100.0}
        return {"f1_valve": 0.0, "f2_valve": 0.0, "purge_valve": 100.0}   # pressure_high / default

    def _toward_safe(self, cmd: ControlCommand, hazard: str, sev: float) -> ControlCommand:
        safe = self.safe_lineup(hazard, cmd)
        return replace(cmd, **{v: getattr(cmd, v) + sev * (tgt - getattr(cmd, v)) for v, tgt in safe.items()})

    def _slew_limit(self, cmd: ControlCommand, dt: float, exempt: Tuple[str, ...] = ()) -> ControlCommand:
        """Authority scales how far a valve may move per cycle. Valves that the
        active hazard is driving toward the safe lineup (`exempt`) are not throttled."""
        if self._last_applied is None or dt <= 0.0:
            return cmd
        step = self.lim.slew_max_pct_per_s * dt * max(self._A, 0.05)
        out = {}
        for v in VALVES:
            new, old = getattr(cmd, v), getattr(self._last_applied, v)
            if v in exempt:
                out[v] = new
                continue
            out[v] = new if abs(new - old) <= step else old + math.copysign(step, new - old)
        return replace(cmd, **out)

    # helpers -----------------------------------------------------------------
    @staticmethod
    def _push(buf: Deque[Tuple[float, float]], t: float, v: float, horizon: float) -> None:
        buf.append((t, v))
        while buf and t - buf[0][0] > horizon:
            buf.popleft()

    def _rate(self, buf: Deque[Tuple[float, float]], t: float) -> Optional[float]:
        """Least-squares slope over the last rate_window_s (robust to sensor noise,
        unlike an endpoint difference)."""
        pts = [(ts, v) for ts, v in buf if t - ts <= self.lim.rate_window_s]
        if len(pts) < 4 or pts[-1][0] - pts[0][0] < self.lim.rate_min_span_s:
            return None
        n = len(pts)
        mt = sum(ts for ts, _ in pts) / n
        mv = sum(v for _, v in pts) / n
        var = sum((ts - mt) ** 2 for ts, _ in pts)
        return 0.0 if var == 0 else sum((ts - mt) * (v - mv) for ts, v in pts) / var
