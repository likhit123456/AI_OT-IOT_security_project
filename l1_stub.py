"""
L1 control stub  --  TEAMMATE-OWNED LAYER. This is only a placeholder.

Your teammate builds the real L1 (regulatory control). This stub exists so you
can run L0 + L3 end-to-end today without waiting for it. It is a minimal set of
PI loops that turn SupervisoryTargets into valve positions. Replace wholesale;
do not build on it.

Mapping used (standard for this reactor):
    pressure  <- purge valve     (open purge -> vent -> pressure down)
    level     <- product valve   (open product -> drain -> level down)
    production<- feed 1 valve     (more feed -> more throughput)
    A comp    <- feed 2 valve     (A-rich feed trim)

Unit note (the L1 seam): the reactor reports A_in_purge as a mole FRACTION
(~0.47), so PlantState.a_in_purge arrives as ~0.47. L3's a_comp_sp is written
in mol PERCENT (contracts.py docstring), e.g. 47.0. Left unconverted, the
comp loop sees a permanent ~46.5 error and saturates feed2 at its high clamp.
L1 is where this gets reconciled: convert the measured fraction to percent
before comparing against the percent target.
"""

from __future__ import annotations

from contracts import PlantState, SupervisoryTargets, ControlCommand


class _PI:
    def __init__(self, kp, ki, lo=0.0, hi=100.0, bias=50.0):
        self.kp, self.ki, self.lo, self.hi = kp, ki, lo, hi
        self.i = 0.0
        self.bias = bias

    def __call__(self, err, dt):
        self.i += err * dt
        out = self.bias + self.kp * err + self.ki * self.i
        out = max(self.lo, min(self.hi, out))
        # anti-windup: clamp integral if saturated
        if out in (self.lo, self.hi):
            self.i -= err * dt
        return out


class L1ControlStub:
    def __init__(self):
        # signs chosen for the actuator mapping above
        self.pressure_pi = _PI(kp=-0.05, ki=-0.002, bias=40.0)   # purge
        self.level_pi    = _PI(kp=-0.8,  ki=-0.02,  bias=60.0)   # product
        self.prod_pi     = _PI(kp=0.3,   ki=0.01,   bias=60.0)   # feed1
        self.comp_pi     = _PI(kp=0.2,   ki=0.005,  bias=50.0)   # feed2

    def regulate(self, targets: SupervisoryTargets, state: PlantState,
                 dt: float) -> ControlCommand:
        purge = self.pressure_pi((targets.pressure_sp or state.pressure) - state.pressure, dt) \
            if targets.pressure_sp is not None else state.purge_valve_pos
        product = self.level_pi((targets.level_sp or state.level) - state.level, dt) \
            if targets.level_sp is not None else state.product_valve_pos
        f1 = self.prod_pi((targets.production_rate_sp or state.product_flow) - state.product_flow, dt) \
            if targets.production_rate_sp is not None else state.f1_valve_pos
        # a_in_purge arrives as a mole fraction (~0.47); a_comp_sp is a mol %
        # target (e.g. 47.0) - convert before comparing (see module docstring).
        a_comp_pct = state.a_in_purge * 100.0
        f2 = self.comp_pi((targets.a_comp_sp or a_comp_pct) - a_comp_pct, dt) \
            if targets.a_comp_sp is not None else state.f2_valve_pos
        return ControlCommand(f1_valve=f1, f2_valve=f2,
                              purge_valve=purge, product_valve=product).clamped()
