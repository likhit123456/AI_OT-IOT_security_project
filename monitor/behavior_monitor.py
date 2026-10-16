"""
Behavior-only anomaly monitor.

This represents the kind of detector a conventional "AI safety" layer would
plausibly ship: it watches the plant's measured telemetry and the setpoints
the operator (L3) is requesting, and flags anything that looks outside a
legal/plausible envelope - soft limits, a rate-of-change-too-HIGH cap, a
per-cycle setpoint-step cap, AND a rate-of-change-too-LOW (staleness/frozen-
sensor) check, the standard sensor-integrity check that catches a naive
stuck-value fault. This is the checklist a sensor-integrity-conscious engineer
would actually ship, not a strawman with an obvious hole left in on purpose.

It is also, by construction, blind to anything that only shows up on the
independent SIS channel (contracts.PlantState.truth). It reads ONLY the
measured fields and the requested SupervisoryTargets - never `.truth` - the
same restriction a real behavior-only/anomaly-based layer would have if it is
built on top of the basic control system's own instrumentation. That is
exactly the limitation the red-team evaluation in
experiments/adversarial_eval.py demonstrates: a sensor fault that holds
measured in-band blinds this monitor just as completely as it blinds a human
operator looking at the same HMI, which is why SCRAM does not rely on this
class (or anything like it) for protective action - see ARCHITECTURE.md.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import List, Optional

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import PlantState, SupervisoryTargets   # noqa: E402


@dataclass(frozen=True)
class MonitorLimits:
    # Soft ceiling, deliberately inside the SIS hard trip (3000 kPa) so a
    # well-behaved monitor would flag an excursion before the real interlock
    # needs to act - if it could see one.
    pressure_max_kpa: float = 2950.0
    level_low_pct: float = 5.0
    level_high_pct: float = 95.0
    # Physically-plausible per-cycle move caps. A real sensor can't jump; a
    # real operator ask shouldn't either.
    max_pressure_rate_kpa: float = 50.0
    max_setpoint_step_kpa: float = 100.0
    # Signal-staleness / frozen-sensor check: a sensor-integrity-conscious
    # engineer checks not just "did it move too much" but "did it fail to
    # move at all while the process was clearly doing something." Flag if
    # pressure's range over the last `staleness_window` cycles is within
    # `staleness_pressure_eps_kpa` of dead flat while either feed/purge valve
    # moved by at least `staleness_valve_move_pct` over the same window.
    staleness_window: int = 10
    staleness_pressure_eps_kpa: float = 0.05
    staleness_valve_move_pct: float = 5.0
    # Level channel gets the same three checks as pressure (band above, plus
    # rate and staleness). Level is a slow, integrating variable, so the rate
    # cap is in % per cycle and the dead-flat epsilon is far tighter than for
    # pressure. "Process clearly doing something" for level = the product
    # valve moved, OR measured product flow is above `staleness_level_active_flow`
    # (liquid is demonstrably leaving the vessel, so a perfectly flat level
    # reading is not credible).
    max_level_rate_pct: float = 2.0
    staleness_level_eps_pct: float = 1e-4
    staleness_level_active_flow: float = 1.0


class BehaviorMonitor:
    """Stateful, one instance per run. Call observe() once per cycle."""

    def __init__(self, limits: Optional[MonitorLimits] = None):
        self.limits = limits or MonitorLimits()
        self._last_pressure: Optional[float] = None
        self._last_pressure_sp: Optional[float] = None
        self.flags: List[str] = []          # every flag ever raised, with cycle index
        self._cycle = 0
        # Bounded (maxlen) rolling windows for the staleness check - never
        # grows past `staleness_window` regardless of run length.
        w = self.limits.staleness_window
        self._pressure_window: deque = deque(maxlen=w)
        self._purge_window: deque = deque(maxlen=w)
        self._f1_window: deque = deque(maxlen=w)
        self._level_window: deque = deque(maxlen=w)
        self._product_window: deque = deque(maxlen=w)
        self._product_flow_window: deque = deque(maxlen=w)
        self._last_level: Optional[float] = None
        # Evidence the level-staleness check actually ran (not vacuous): how
        # many cycles it was evaluated with the process "active", and how
        # many of those it flagged.
        self.level_stale_evaluated = 0
        self.level_stale_flagged = 0

    @property
    def ever_flagged(self) -> bool:
        return bool(self.flags)

    def observe(self, state: PlantState, targets: SupervisoryTargets) -> List[str]:
        """One cycle. Returns the (possibly empty) list of flags raised now."""
        self._cycle += 1
        lim = self.limits
        raised: List[str] = []

        if state.pressure > lim.pressure_max_kpa:
            raised.append(
                f"measured pressure {state.pressure:.1f} kPa above soft ceiling "
                f"{lim.pressure_max_kpa:.0f}"
            )
        if not (lim.level_low_pct <= state.level <= lim.level_high_pct):
            raised.append(
                f"measured level {state.level:.1f}% outside "
                f"[{lim.level_low_pct:.0f},{lim.level_high_pct:.0f}]"
            )
        if self._last_pressure is not None:
            rate = abs(state.pressure - self._last_pressure)
            if rate > lim.max_pressure_rate_kpa:
                raised.append(
                    f"measured pressure moved {rate:.1f} kPa in one cycle "
                    f"(> {lim.max_pressure_rate_kpa:.0f})"
                )
        if targets.pressure_sp is not None:
            if targets.pressure_sp > lim.pressure_max_kpa:
                raised.append(
                    f"requested pressure_sp {targets.pressure_sp:.1f} kPa above "
                    f"legal ceiling {lim.pressure_max_kpa:.0f}"
                )
            if self._last_pressure_sp is not None:
                step = abs(targets.pressure_sp - self._last_pressure_sp)
                if step > lim.max_setpoint_step_kpa:
                    raised.append(
                        f"pressure_sp jumped {step:.1f} kPa in one cycle "
                        f"(> {lim.max_setpoint_step_kpa:.0f})"
                    )
            self._last_pressure_sp = targets.pressure_sp

        if self._last_level is not None:
            lrate = abs(state.level - self._last_level)
            if lrate > lim.max_level_rate_pct:
                raised.append(
                    f"measured level moved {lrate:.2f}% in one cycle "
                    f"(> {lim.max_level_rate_pct:.1f})"
                )

        self._level_window.append(state.level)
        self._product_window.append(state.product_valve_pos)
        self._product_flow_window.append(state.product_flow)
        if len(self._level_window) == lim.staleness_window:
            level_range = max(self._level_window) - min(self._level_window)
            product_range = max(self._product_window) - min(self._product_window)
            active = (product_range >= lim.staleness_valve_move_pct
                      or min(self._product_flow_window) > lim.staleness_level_active_flow)
            if active:
                self.level_stale_evaluated += 1
                if level_range <= lim.staleness_level_eps_pct:
                    self.level_stale_flagged += 1
                    raised.append(
                        f"measured level frozen (range {level_range:.5f}% over last "
                        f"{lim.staleness_window} cycles) while process active "
                        f"(product valve moved {product_range:.1f}%, "
                        f"min product flow {min(self._product_flow_window):.1f})"
                    )
        self._last_level = state.level

        self._pressure_window.append(state.pressure)
        self._purge_window.append(state.purge_valve_pos)
        self._f1_window.append(state.f1_valve_pos)
        if len(self._pressure_window) == lim.staleness_window:
            pressure_range = max(self._pressure_window) - min(self._pressure_window)
            purge_range = max(self._purge_window) - min(self._purge_window)
            f1_range = max(self._f1_window) - min(self._f1_window)
            valves_moved = max(purge_range, f1_range) >= lim.staleness_valve_move_pct
            if pressure_range <= lim.staleness_pressure_eps_kpa and valves_moved:
                raised.append(
                    f"measured pressure frozen (range {pressure_range:.3f} kPa over last "
                    f"{lim.staleness_window} cycles) while valves moved "
                    f"(purge {purge_range:.1f}%, f1 {f1_range:.1f}%)"
                )

        self._last_pressure = state.pressure
        if raised:
            self.flags.extend(f"[cycle {self._cycle}] {f}" for f in raised)
        return raised
