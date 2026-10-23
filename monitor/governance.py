"""
L2 governance: turns the L2 monitors' suspicion into an authority CEILING for the
AI operator - the "when do we throttle the brain" decision.

This SHAPES authority; it does not provide safety. The SIS (l0_plant/safety.py)
stays the independent hard stop and Keel keeps its own physics / gate-driven
authority; the operator gets min(Keel's authority, this ceiling).

Inputs each cycle (routed exactly as in Phase 1):
  * the rule monitor's score (SequentialMonitor), sampled every RULE_SAMPLE_S
  * the observer's PROPOSAL-side verdicts (provenance_violation,
    rationale_mismatch, setpoint_drift); telemetry verdicts are display-only
  * the brain's latest pre-blend intent, for the recovery envelope check

1. Debounce: suspicion only counts once corroborated - two CONSECUTIVE calls of
   the same monitor both above TIER1 (for the observer: two consecutive verdicts
   that are proposal-side attack labels). A one-off spike moves nothing.
   Corroboration is temporal per monitor, not cross-monitor agreement, so the
   observer can still catch what the rule monitor cannot see.
2. Ceiling g(s): piecewise, monotonic non-increasing, bounded in [floor, 1]; the
   floor is NONZERO so no verdict (single, repeated or hallucinated) can drive the
   operator to 0.
3. Rates: the applied ceiling falls toward g(s) fast (DROP_PER_S) and rises slowly
   (RECOVER_PER_S), and only after suspicion has stayed below TIER1 for
   CLEAN_WINDOW_S AND the operator's latest proposal is back inside the design
   envelope - recovery is not just history aging out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from l3_brain.prompt_format import DESIGN_POINT

PROPOSAL_LABELS = ("provenance_violation", "rationale_mismatch", "setpoint_drift")

# ceiling breakpoints: s < TIER1 -> 1.0; TIER1..TIER2 -> 1.0..CEIL_TIER2;
# TIER2..TIER3 -> CEIL_TIER2..CEIL_TIER3; s >= TIER3 -> FLOOR (FLOOR_PROVENANCE)
TIER1, TIER2, TIER3 = 0.30, 0.60, 0.90
CEIL_TIER2, CEIL_TIER3 = 0.55, 0.25
FLOOR, FLOOR_PROVENANCE = 0.15, 0.10

DROP_PER_S = 0.25             # applied ceiling may fall this fast (1.0 -> 0.15 in ~3.4 s)
RECOVER_PER_S = 0.03          # ... and rise only this fast
CLEAN_WINDOW_S = 15.0         # suspicion below TIER1 this long before any recovery
RULE_SAMPLE_S = 1.0           # one rule-monitor "call" per second (it scores every cycle)
# Once the operator's proposals are back inside the design envelope, the rule monitor's
# RESIDUAL evidence (its CUSUM fades with a 120 s half-life, by design, so slow attacks
# accumulate) is discounted with this much shorter half-life: 0.9 -> below TIER1 in
# ~14 s instead of ~190 s. Outside the envelope (during an attack) the raw score is used
# unchanged, and any NEW rise in the raw score counts in full even inside the envelope.
# Only the ACCUMULATED sources are residual; per-cycle checks (rationale patterns, Keel
# codes) are re-evaluated every cycle and always count in full.
RESIDUAL_HALF_LIFE_S = 9.0
# Observer evidence expires: with no verdict for this long (model down, path fallen back to
# rule-monitor-only, observer reset) its corroborated suspicion no longer holds the ceiling.
# Proposals are checked every ~5 s, so this is several missed calls.
OBS_STALE_S = 30.0
RESIDUAL_SOURCES = ("cusum", "run")

# design envelope for recovery: the operator's proposal must be this close to design
ENVELOPE = {"pressure_sp": 30.0, "level_sp": 2.0, "production_rate_sp": 5.0}
_DESIGN_SP = {"pressure_sp": DESIGN_POINT["pressure"], "level_sp": DESIGN_POINT["level"],
              "production_rate_sp": DESIGN_POINT["production"]}


def ceiling(s: float, label: Optional[str] = None) -> float:
    """g(s): corroborated suspicion -> authority ceiling."""
    s = max(0.0, min(1.0, s))
    if s < TIER1:
        return 1.0
    if s < TIER2:
        return 1.0 + (CEIL_TIER2 - 1.0) * (s - TIER1) / (TIER2 - TIER1)
    if s < TIER3:
        return CEIL_TIER2 + (CEIL_TIER3 - CEIL_TIER2) * (s - TIER2) / (TIER3 - TIER2)
    return FLOOR_PROVENANCE if label == "provenance_violation" else FLOOR


def in_envelope(intent) -> bool:
    """True when the proposal is back near design; a hold (None) counts as inside."""
    if intent is None:
        return True
    for k, tol in ENVELOPE.items():
        v = getattr(intent, k, None)
        if v is not None and abs(v - _DESIGN_SP[k]) > tol:
            return False
    return True


@dataclass
class GovState:
    t: float
    raw: float                # max(rule score, observer proposal suspicion), uncorroborated
    corroborated: float
    label: Optional[str]      # what drives the corroborated value
    ceiling: float            # g(corroborated)
    applied: float            # rate-limited ceiling handed to the operator
    recovering: bool
    envelope_ok: bool

    def as_dict(self) -> dict:
        return {"t": round(self.t, 2), "raw": round(self.raw, 3), "corroborated": round(self.corroborated, 3),
                "label": self.label, "ceiling": round(self.ceiling, 3), "applied": round(self.applied, 3),
                "recovering": self.recovering, "envelope_ok": self.envelope_ok}


class Governor:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.applied = 1.0
        self._last_t: Optional[float] = None
        self._obs_prev: Optional[tuple] = None        # (label, score) of the previous verdict
        self._obs_corr, self._obs_label = 0.0, None
        self._obs_seen_t: Optional[float] = None
        self._rule_prev: Optional[float] = None
        self._rule_corr, self._rule_label = 0.0, None
        self._rule_sample_t = -1e9
        self._rule_eff: Optional[float] = None       # rule score after the residual discount
        self._rule_raw_prev: Optional[float] = None
        self._clean_since: Optional[float] = None
        self.last: Optional[GovState] = None

    def _observer(self, verdict, t: float) -> None:
        """One observer call = one new proposals verdict (invalid ones are skipped)."""
        if verdict is None or t - verdict.t > OBS_STALE_S:
            # no observer evidence (none yet, model gone, or stale): it holds nothing
            self._obs_corr, self._obs_label, self._obs_prev = 0.0, None, None
            return
        if verdict.label is None or verdict.t == self._obs_seen_t:
            return
        self._obs_seen_t = verdict.t
        cur = (verdict.label, verdict.score) if verdict.label in PROPOSAL_LABELS else None
        if cur is None:                                       # a normal verdict clears it at once
            self._obs_corr, self._obs_label = 0.0, None
        elif self._obs_prev is not None and min(self._obs_prev[1], cur[1]) >= TIER1:
            self._obs_corr, self._obs_label = min(self._obs_prev[1], cur[1]), cur[0]
        self._obs_prev = cur

    def _rule(self, t: float, raw: float, source: str, env: bool) -> None:
        if t - self._rule_sample_t < RULE_SAMPLE_S:
            return
        dt, self._rule_sample_t = t - self._rule_sample_t, t
        if env and source in RESIDUAL_SOURCES and self._rule_eff is not None:
            fresh = max(0.0, raw - self._rule_raw_prev)          # new evidence counts in full
            score = min(raw, self._rule_eff * 0.5 ** (dt / RESIDUAL_HALF_LIFE_S) + fresh)
        else:
            score = raw
        self._rule_eff, self._rule_raw_prev = score, raw
        if self._rule_prev is not None and min(self._rule_prev, score) >= TIER1:
            self._rule_corr, self._rule_label = min(self._rule_prev, score), f"rule:{source}"
        elif score < TIER1:
            self._rule_corr, self._rule_label = 0.0, None
        self._rule_prev = score

    def update(self, t: float, rule_score: float, rule_source: str, obs_verdict=None,
               obs_suspicion: float = 0.0, intent=None) -> GovState:
        dt = 0.0 if self._last_t is None else max(0.0, min(t - self._last_t, 5.0))
        self._last_t = t
        self._observer(obs_verdict, t)
        env = in_envelope(intent)
        self._rule(t, rule_score, rule_source, env)
        if self._obs_corr >= self._rule_corr:
            s, label = self._obs_corr, self._obs_label
        else:
            s, label = self._rule_corr, self._rule_label
        g = ceiling(s, label)
        if s < TIER1:
            self._clean_since = t if self._clean_since is None else self._clean_since
        else:
            self._clean_since = None
        recovering = False
        if g < self.applied:
            self.applied = max(g, self.applied - DROP_PER_S * dt)
        elif g > self.applied and env and self._clean_since is not None \
                and t - self._clean_since >= CLEAN_WINDOW_S:
            self.applied = min(g, self.applied + RECOVER_PER_S * dt)
            recovering = True
        self.last = GovState(t, max(rule_score, obs_suspicion), s, label if s > 0 else None,
                             g, self.applied, recovering, env)
        return self.last
