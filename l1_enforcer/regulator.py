"""
L1 regulatory control: four PI loops that turn SupervisoryTargets into valve
positions, and the signer that stamps every outgoing command for the
provenance gate. Replaces l1_stub.py + live/engine.LiveL1 on the live path
(the offline experiments keep the stub their saved results were produced with).

Actuator mapping for the TE reactor (primary actuator per variable):
    pressure    <- purge valve      (open purge -> vent -> pressure falls).
                   Split range: at the design point purge sits almost shut
                   (~0.4 %), so when the loop output goes below 0 it raises
                   both feeds instead: u in [-FEED_SPAN, 0) maps linearly to a
                   feed trim of 0..+FEED_TRIM_MAX. That is the only way to build
                   pressure back after a vent / e-stop.
    level       <- product valve    (open product -> drain -> level falls)
    production  <- feed 1 valve     feedforward f1 = design f1 x sp/100, plus a
                   SLOW PI trim on measured product flow
    A in purge  <- feed 2 valve     (A-rich feed)

Decoupling is by time scale, not by a model: level is the fast loop on the
product valve; production only trims feed 1 slowly (outflow follows inflow
through the level loop at steady state), so the two do not fight. Pressure
uses purge (and the feeds only when purge is saturated shut); while it trims the
feeds, the production trim is held, so it neither cuts feed 1 back against the
pressure build-up nor winds up on the throughput change it causes.

Units (the L1 seam): valves are percent [0, 100] (contracts.ControlCommand);
a_comp_sp is mol PERCENT (e.g. 47.0) while the plant reports a_in_purge as a
mole FRACTION (~0.47) - converted here, in one place (_a_pct).

Every integrator has conditional-integration anti-windup and every output is
clamped. Integration uses the real elapsed time the caller passes in (the old
stub integrated a fixed 0.5 s per ~0.06 s cycle, ~8x too fast). Bumpless
transfer: bumpless(state) re-seeds every integrator so the next output equals
the valve positions the plant already holds (call on connect / re-arm); a loop
whose setpoint goes None (authority 0 = hold) freezes its valve and re-seeds
itself from that valve when a setpoint returns.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from shared.contracts import ControlCommand, PlantState, SupervisoryTargets
from l0_plant.provenance_gate import CommandSigner

DESIGN_F1, DESIGN_F2, DESIGN_PURGE, DESIGN_PRODUCT = 31.45, 13.24, 0.3926, 47.03
DESIGN_PRODUCTION = 100.0
FEED_TRIM_MAX = 0.50            # split range: feeds may rise at most +50 % to build pressure
FEED_SPAN = 6.0                 # pressure-loop output units spanning the whole feed-trim range

# gains: kp in valve-% per unit error, ki in valve-% per unit error per second
PRESSURE_KP, PRESSURE_KI = 0.30, 0.003       # per kPa (error = measured - setpoint)
LEVEL_KP, LEVEL_KI = 4.0, 0.04               # per %   (error = measured - setpoint)
PRODUCTION_KP, PRODUCTION_KI = 0.02, 0.002   # per kmol/h (error = setpoint - measured), slow trim
COMP_KP, COMP_KI = 1.5, 0.02                 # per mol % (error = setpoint - measured)


class PI:
    """u = kp*e + i, clamped to [lo, hi]; the integral only moves when that does
    not push further into saturation (conditional integration)."""

    def __init__(self, kp: float, ki: float, lo: float, hi: float, init: float = 0.0):
        self.kp, self.ki, self.lo, self.hi = kp, ki, lo, hi
        self.i = init

    def __call__(self, e: float, dt: float) -> float:
        u = self.kp * e + self.i
        if not ((u >= self.hi and e > 0) or (u <= self.lo and e < 0)):
            self.i += self.ki * e * dt
        return max(self.lo, min(self.hi, self.kp * e + self.i))

    def track(self, u: float, e: float = 0.0) -> None:
        """Bumpless: make the current output equal u."""
        self.i = u - self.kp * e


def _a_pct(state) -> float:
    return state.a_in_purge * 100.0          # fraction -> mol %, the unit a_comp_sp is written in


class L1Regulator:
    def __init__(self, key: Optional[bytes] = None):
        # split-range pressure output: u in [-FEED_SPAN, 100]; u >= 0 is purge %, u < 0 is feed trim
        self.pressure = PI(PRESSURE_KP, PRESSURE_KI, -FEED_SPAN, 100.0, DESIGN_PURGE)
        self.level = PI(LEVEL_KP, LEVEL_KI, 0.0, 100.0, DESIGN_PRODUCT)
        self.production = PI(PRODUCTION_KP, PRODUCTION_KI, -15.0, 15.0, 0.0)   # f1 trim, valve %
        self.comp = PI(COMP_KP, COMP_KI, 0.0, 100.0, DESIGN_F2)
        self._prod_out = 0.0
        self._held = {"pressure": False, "level": False, "production": False, "comp": False}
        self.signer = CommandSigner(key) if key is not None else None

    # ---- the four loops ---------------------------------------------------------
    def regulate(self, targets: SupervisoryTargets, state: PlantState, dt: float) -> ControlCommand:
        dt = max(0.0, min(float(dt), 1.0))
        # pressure (split range: purge, then feeds)
        if self._resume("pressure", targets.pressure_sp, state):
            u = self.pressure(state.pressure - targets.pressure_sp, dt)
        else:
            u = state.purge_valve_pos
        purge = max(0.0, u)
        trim = 1.0 + FEED_TRIM_MAX * max(0.0, -u) / FEED_SPAN
        # level
        if self._resume("level", targets.level_sp, state):
            product = self.level(state.level - targets.level_sp, dt)
        else:
            product = state.product_valve_pos
        # production: feedforward + slow trim on feed 1
        if self._resume("production", targets.production_rate_sp, state):
            ff = DESIGN_F1 * targets.production_rate_sp / DESIGN_PRODUCTION
            if trim > 1.0:
                # the pressure loop is borrowing the feeds: hold the production trim (do not
                # integrate against throughput the feed trim is deliberately changing)
                f1 = ff + self._prod_out
            else:
                self._prod_out = self.production(targets.production_rate_sp - state.product_flow, dt)
                f1 = ff + self._prod_out
        else:
            f1 = state.f1_valve_pos / trim
        # composition
        if self._resume("comp", targets.a_comp_sp, state):
            f2 = self.comp(targets.a_comp_sp - _a_pct(state), dt)
        else:
            f2 = state.f2_valve_pos / trim
        return ControlCommand(f1_valve=f1 * trim, f2_valve=f2 * trim,
                              purge_valve=purge, product_valve=product).clamped()

    def _resume(self, loop: str, sp, state: PlantState) -> bool:
        """None setpoint -> hold this valve. When a setpoint returns after a hold, seed the
        loop from the valve the plant holds now so the hand-back does not kick."""
        if sp is None:
            self._held[loop] = True
            return False
        if self._held[loop]:
            self._held[loop] = False
            self._seed(loop, state, sp)
        return True

    def _seed(self, loop: str, state: PlantState, sp: Optional[float]) -> None:
        if loop == "pressure":
            e = 0.0 if sp is None else state.pressure - sp
            self.pressure.track(state.purge_valve_pos, e)
        elif loop == "level":
            e = 0.0 if sp is None else state.level - sp
            self.level.track(state.product_valve_pos, e)
        elif loop == "production":
            q = DESIGN_PRODUCTION if sp is None else sp
            self._prod_out = state.f1_valve_pos - DESIGN_F1 * q / DESIGN_PRODUCTION
            self.production.track(self._prod_out, q - state.product_flow)
        else:
            e = 0.0 if sp is None else sp - _a_pct(state)
            self.comp.track(state.f2_valve_pos, e)

    def bumpless(self, state: PlantState, targets: Optional[SupervisoryTargets] = None) -> None:
        """Re-seed every loop from the valves the plant holds now (connect, re-arm, reconnect)."""
        t = targets or SupervisoryTargets()
        for loop, sp in (("pressure", t.pressure_sp), ("level", t.level_sp),
                         ("production", t.production_rate_sp), ("comp", t.a_comp_sp)):
            self._seed(loop, state, sp)
            self._held[loop] = False

    # ---- provenance ----------------------------------------------------------------
    def sign(self, cmd: ControlCommand) -> ControlCommand:
        """Stamp the FINAL command (after the enforcer's clamps) for the provenance gate."""
        if self.signer is None:
            raise RuntimeError("L1Regulator has no signing key")
        return self.signer.sign(replace(cmd, seq=0, auth_tag=""))
