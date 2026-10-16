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
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _valid(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


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
    def __init__(self, limits: TripLimits | None = None):
        self.limits = limits or TripLimits()
        self.tripped = False
        self.reason = ""
        self.cause = ""     # "pressure_high" | "level_low" | "level_high" while latched

    def evaluate(self, pressure: float, level: float) -> bool:
        """
        Update interlock state from the latest measured values.
        Returns True if the plant should currently be forced safe (scrammed).
        """
        lim = self.limits
        if not self.tripped:
            if not (_valid(pressure) and _valid(level)):
                self._trip(f"safety-channel reading invalid (pressure={pressure!r}, level={level!r})",
                           "sis_sensor_fault")
            elif pressure >= lim.pressure_high:
                self._trip(f"pressure {pressure:.0f} >= {lim.pressure_high:.0f} kPa", "pressure_high")
            elif level <= lim.level_low:
                self._trip(f"level {level:.1f}% <= {lim.level_low:.1f}%", "level_low")
            elif level >= lim.level_high:
                self._trip(f"level {level:.1f}% >= {lim.level_high:.1f}%", "level_high")
        return self.tripped

    def can_reset(self, pressure: float, level: float) -> bool:
        """Whether a latched trip is eligible to be cleared right now."""
        lim = self.limits
        if not (_valid(pressure) and _valid(level)):
            return False
        return (pressure <= lim.pressure_rearm
                and lim.level_low_rearm <= level <= lim.level_high_rearm)

    def reset(self, pressure: float, level: float) -> bool:
        """
        Deliberate operator reset. Only succeeds if variables are back inside
        the re-arm band; otherwise the trip stays latched.
        """
        if self.tripped and self.can_reset(pressure, level):
            self.tripped = False
            self.reason = ""
            self.cause = ""
            return True
        return False

    def safe_state_inputs(self) -> dict:
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
        """
        if self.cause == "level_low":
            return {"e_stop": 0, "product_valve_sp": 0.0}
        return {"e_stop": 1}

    def _trip(self, reason: str, cause: str) -> None:
        self.tripped = True
        self.reason = reason
        self.cause = cause
