"""
L3 AI operator ("Brain").

Owns the operating loop. Each cycle it:
  1. takes the latest PlantState (handed down from L2/supervisory),
  2. asks its Policy for SupervisoryTargets,
  3. applies the governance authority clamp to those targets,
  4. emits the targets downward (to L1, through governance).

It deliberately does NOT talk to L0 or to valves. Its only output is intent.

Governance seam: `authority_fn` is a callable the governance layer (teammate,
L1/L2 boundary) provides; it returns the current 0..1 authority. Here we accept
it as an injected dependency so the brain has no hard dependency on a layer it
doesn't own. If none is supplied we assume full authority (useful for local
L0+L3 testing before governance exists).

Hard rule: this handshake (and the policy it wraps) reads ONLY
PlantState's measured fields - pressure, level, product_flow, a_in_purge,
valve positions. It never reads `.truth` (the independent SIS channel); that
channel belongs solely to L0's interlock (l0_plant/safety.py). L3 is
detection/governance logic operating on the same (possibly faulted) view the
basic control system sees - the interlock is the only thing with eyes on
ground truth, and it enforces safety regardless of what L3 asks for.

Server-side model calls: if you later back the policy with a hosted model, make
that call inside Policy.decide on the server - never ship keys/model calls to a
client. The loop here is already server-side.
"""

from __future__ import annotations

import sys
import os
from dataclasses import replace
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import PlantState, SupervisoryTargets   # noqa: E402
from l3_brain.policy import Policy, BaselineOperator, make_policy   # noqa: E402,F401


class AIOperator:
    def __init__(self,
                 policy: Optional[Policy] = None,
                 authority_fn: Optional[Callable[[], float]] = None):
        self.policy = policy or make_policy()     # SCRAM_POLICY=baseline|llm (default baseline)
        self.authority_fn = authority_fn or (lambda: 1.0)

    def _apply_authority(self, targets: SupervisoryTargets,
                         state: PlantState) -> SupervisoryTargets:
        """
        Blend the brain's requested setpoints toward a safe hold as authority
        drops. authority=1 -> full request, unchanged. authority=0 -> hold,
        meaning every setpoint goes to None: L1 (l1_stub.regulate) treats a
        None setpoint as "leave that valve where it is", which is a true
        freeze, not a PI loop quietly re-targeting the current reading. In
        between, setpoints are interpolated between "current measured value"
        (0 net change requested) and the full request, so the brain's
        aggressiveness fades smoothly rather than snapping at some threshold.
        This is the brain's own conservatism; the hard clamp is still enforced
        downstream by governance/L1 and, ultimately, by L0's interlock, which
        acts on the independent SIS channel regardless of what L3 asks for.
        """
        a = max(0.0, min(1.0, self.authority_fn()))
        if a >= 1.0:
            return replace(targets, authority=a)
        if a <= 0.0:
            return replace(
                targets,
                pressure_sp=None, level_sp=None,
                production_rate_sp=None, a_comp_sp=None,
                authority=0.0,
                rationale=targets.rationale + " [authority 0.00: holding, no setpoints issued]",
            )

        def blend(req: Optional[float], current: float) -> Optional[float]:
            if req is None:
                return None
            return current * (1 - a) + req * a

        return replace(
            targets,
            pressure_sp=blend(targets.pressure_sp, state.pressure),
            level_sp=blend(targets.level_sp, state.level),
            production_rate_sp=blend(targets.production_rate_sp, state.product_flow),
            # a_in_purge is a mole FRACTION (~0.47); a_comp_sp is mol PERCENT
            # (e.g. 47.0) - see l1_stub.py's module docstring for the same
            # reconciliation. Blend in percent so "current" and "request" are
            # the same unit.
            a_comp_sp=blend(targets.a_comp_sp, state.a_in_purge * 100.0),
            authority=a,
            rationale=targets.rationale + f" [authority {a:.2f}]",
        )

    def step(self, state: PlantState) -> SupervisoryTargets:
        """One decision cycle. Pure function of state + current authority."""
        targets = self.policy.decide(state)
        return self._apply_authority(targets, state)


def stub_authority_fn() -> float:
    """
    Placeholder for the real L2 governance authority_fn - fixed at full
    authority. The L2 teammate replaces this with whatever actually detects
    "the brain is misbehaving" (e.g. anomaly score, policy confidence, a human
    override switch) and returns a 0..1 value from it; everything downstream
    in AIOperator already knows what to do with that number.
    """
    return 1.0
