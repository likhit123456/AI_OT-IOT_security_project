"""
L0 hardware safety layer - the independent interlock ("SCRAM").

This is the bottom of the SCRAM thesis: a last-resort protective layer that
sits UNDER the AI and cannot be talked out of tripping. It does not know or
care what L3 intends or how much authority governance granted; it watches a
handful of physical variables and, if any crosses a hard limit, forces the
plant into a safe state (feeds shut, relief paths open) by asserting the
simulator's e_stop.

Design rules:
  * Trip logic depends ONLY on physical state, never on upstream intent. The
    caller (plant_service.py) is responsible for handing this class the
    reactor's "outputs" - modeled as a dedicated safety-instrumented sensor
    path, independent of the basic control system - not the "measured" view
    the basic control system itself sees. This assumes standard SIS
    independence (the safety-instrumented path is out of the attacker's
    reach); see ARCHITECTURE.md.
  * Trips latch. Once tripped, the plant stays safed until an explicit,
    deliberate reset (a human/operator action in the real thing) AND the
    variables are back inside the re-arm band - exactly like a real reactor
    scram that you cannot clear while still over-pressure.
  * Limits here are protective trip points, set wider than the normal
    operating envelope L1 regulates within, so the interlock only fires on a
    genuine excursion - not on routine control action.
  * A safety-channel reading that is missing or not a finite number trips the
    interlock (cause "sis_sensor_fault", safe state e_stop): an SIS that cannot
    read its own sensor fails safe rather than assuming all is well.
  * Optional closed-loop safe-hold (SafeHold): instead of an e_stop that kills
    the reaction, a pressure / high-level trip cuts the feeds back and works
    purge and product on the safety channel until the plant is back inside the
    re-arm band, still running. It escalates to e_stop on a runaway
    (pressure >= SafeHold.runaway_kpa) and stays there until reset.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _valid(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# Valve lineup of the settled design point (training/design_point.json)
DESIGN_VALVES = {"f1": 31.45, "f2": 13.24, "purge": 0.39, "product": 47.03}


@dataclass(frozen=True)
class SafeHold:
    """Closed-loop safe-hold law, run by the SIS on its own (safety-channel) readings."""
    feed_frac: float = 0.6        # feeds cut to this fraction of the design lineup
    p_target: float = 2650.0      # kPa: purge opens in proportion to pressure above this
    kp_purge: float = 0.4         # % purge per kPa
    l_target: float = 50.0        # %: product opens in proportion to level above this
    kl_product: float = 3.0       # % product per % level
    runaway_kpa: float = 3050.0   # still climbing past the trip: give up and e_stop

    def lineup(self, pressure: float, level: float) -> dict:
        clamp = lambda v: round(max(0.0, min(100.0, v)), 2)
        d = DESIGN_VALVES
        return {"e_stop": 0,
                "f1_valve_sp": clamp(d["f1"] * self.feed_frac),
                "f2_valve_sp": clamp(d["f2"] * self.feed_frac),
                "purge_valve_sp": clamp(d["purge"] + self.kp_purge * (pressure - self.p_target)),
                "product_valve_sp": clamp(d["product"] * self.feed_frac + self.kl_product * (level - self.l_target))}


@dataclass(frozen=True)
class TripLimits:
    # High reactor pressure is the dominant hazard in this process; the model
    # itself hard-clamps at 3200 kPa, so trip well below that.
    pressure_high: float = 3000.0      # kPa
    # Loss of liquid level risks pump damage / loss of cooling; overfill risks
    # carryover. Protective band sits outside the normal 30-70% operating range.
    level_low: float = 5.0             # %
    level_high: float = 95.0           # %
    # Re-arm hysteresis: variables must return comfortably inside these before
    # a latched trip can be cleared.
    pressure_rearm: float = 2800.0     # kPa
    level_low_rearm: float = 15.0      # %
    level_high_rearm: float = 85.0     # %


class SafetyInterlock:
    def __init__(self, limits: TripLimits | None = None, safe_hold: SafeHold | None = None):
        self.limits = limits or TripLimits()
        self.safe_hold = safe_hold          # None = classic e_stop safe state
        self.tripped = False
        self.reason = ""
        self.cause = ""     # "pressure_high" | "level_low" | "level_high" while latched
        self.escalated = False              # safe-hold gave up on a runaway: e_stop until reset
        self._sensor_fault = False
        self._combined_low_level = False
        self._pressure_seen = False

    def evaluate(self, pressure: float, level: float) -> bool:
        """
        Update interlock state from the latest measured values.
        Returns True if the plant should currently be forced safe (scrammed).
        """
        lim = self.limits
        valid = _valid(pressure) and _valid(level)
        if not self.tripped:
            if not valid:
                self._trip(f"safety-channel reading invalid (pressure={pressure!r}, level={level!r})",
                           "sis_sensor_fault")
            elif pressure >= lim.pressure_high:
                self._trip(f"pressure {pressure:.0f} >= {lim.pressure_high:.0f} kPa", "pressure_high")
            elif level <= lim.level_low:
                self._trip(f"level {level:.1f}% <= {lim.level_low:.1f}%", "level_low")
            elif level >= lim.level_high:
                self._trip(f"level {level:.1f}% >= {lim.level_high:.1f}%", "level_high")
        if self.tripped:
            # Preserve the first cause for diagnosis, but never freeze hazard detection.
            self._sensor_fault |= not valid
            if valid:
                self._pressure_seen |= pressure >= lim.pressure_high
                if level <= lim.level_low and self._pressure_seen:
                    self._combined_low_level = True
                if self.cause == "level_low" and pressure >= lim.pressure_high:
                    self._combined_low_level = True
                if self.cause == "level_low" and level >= lim.level_high:
                    self.escalated = True
            self.escalated |= self._sensor_fault or self._combined_low_level
        return self.tripped

    def can_reset(self, pressure: float, level: float) -> bool:
        """Whether a latched trip is eligible to be cleared right now."""
        lim = self.limits
        if not (_valid(pressure) and _valid(level)):
            return False
        return (pressure <= lim.pressure_rearm
                and lim.level_low_rearm <= level <= lim.level_high_rearm)

    @property
    def protection_mode(self) -> str:
        if not self.tripped:
            return "armed"
        if self._sensor_fault:
            return "emergency_stop"
        if self._combined_low_level:
            return "combined_hold"
        if self.cause == "level_low" and not self.escalated:
            return "inventory_hold"
        if self.safe_hold is not None and not self.escalated:
            return "safe_hold"
        return "emergency_stop"

    def reset(self, pressure: float, level: float) -> bool:
        """
        Deliberate operator reset. Only succeeds if variables are back inside
        the re-arm band; otherwise the trip stays latched.
        """
        if self.tripped and self.can_reset(pressure, level):
            self.tripped = False
            self.reason = ""
            self.cause = ""
            self.escalated = False
            self._sensor_fault = self._combined_low_level = self._pressure_seen = False
            return True
        return False

    def safe_state_inputs(self, pressure: float | None = None, level: float | None = None) -> dict:
        """
        Simulator write-inputs that put the plant in the safe state for the
        latched trip cause. The right safe state depends on the hazard:

          * pressure_high / level_high: e_stop (feeds shut, purge and product
            open) - relieves pressure and drains an overfull vessel.
          * level_low: e_stop would OPEN the product valve and keep draining a
            vessel that is already too low. Instead close product to stop the
            drain and leave the feed/purge valves where they are (they are
            simply not written, so the simulator holds them) so inflow and
            reaction make the level recover toward the re-arm band.

        With a SafeHold configured and valid safety-channel readings passed in,
        pressure_high / level_high run the closed-loop safe-hold lineup instead
        of e_stop, escalating (latched) to e_stop on a runaway.
        """
        if self._sensor_fault:
            return {"e_stop": 1}
        if self._combined_low_level:
            # e_stop opens product in this simulator, aggravating the low inventory.
            # Remove feed energy, vent vapour, and retain the remaining liquid.
            return {"e_stop": 0, "f1_valve_sp": 0.0, "f2_valve_sp": 0.0,
                    "purge_valve_sp": 100.0, "product_valve_sp": 0.0}
        if self.cause == "level_low" and not self.escalated:
            return {"e_stop": 0, "product_valve_sp": 0.0}
        hold = self.safe_hold
        if (hold is not None and self.cause in ("pressure_high", "level_high")
                and _valid(pressure) and _valid(level)):
            if pressure >= hold.runaway_kpa:
                self.escalated = True
            if not self.escalated:
                return hold.lineup(pressure, level)
        return {"e_stop": 1}

    def _trip(self, reason: str, cause: str) -> None:
        self.tripped = True
        self.reason = reason
        self.cause = cause
