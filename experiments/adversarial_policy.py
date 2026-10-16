"""
Scripted "adversarial" L3 policy for the red-team evaluation
(experiments/adversarial_eval.py) only - this is not a real L3 policy and is
not wired into run_demo.py.

It issues a smoothly ramped pressure/production setpoint sequence. Each
individual request is unremarkable on its own - it never asks for anything
above the monitor's own legal ceiling (monitor.behavior_monitor.MonitorLimits
.pressure_max_kpa) and never jumps by more than a plausible step per cycle.
It stands in for an operator (human or AI) that is simply pushing too hard,
not for a compromised/malicious L3 - the point of the evaluation is that this
"merely too aggressive, but individually legal" ask, combined with a frozen
pressure sensor, is already enough to blind a behavior-only monitor and drive
the plant toward the hard limit, with nothing unusual for such a monitor to
catch anywhere in the request stream.
"""

from __future__ import annotations

from contracts import PlantState, SupervisoryTargets


class AdversarialRamp:
    def __init__(self, start_kpa: float = 2700.0, plateau_kpa: float = 2900.0,
                 ramp_cycles: int = 30, production_target: float = 140.0,
                 a_comp_target: float = 47.0, level_target: float = 50.0):
        self.start_kpa = start_kpa
        self.plateau_kpa = plateau_kpa
        self.ramp_cycles = ramp_cycles
        self.production_target = production_target
        self.a_comp_target = a_comp_target
        self.level_target = level_target
        self._k = 0

    def decide(self, state: PlantState) -> SupervisoryTargets:
        self._k += 1
        frac = min(1.0, self._k / self.ramp_cycles)
        pressure_sp = self.start_kpa + frac * (self.plateau_kpa - self.start_kpa)
        return SupervisoryTargets(
            pressure_sp=pressure_sp,
            level_sp=self.level_target,
            production_rate_sp=self.production_target,
            a_comp_sp=self.a_comp_target,
            authority=1.0,
            rationale=f"ramping toward {pressure_sp:.0f} kPa plateau (cycle {self._k})",
        )
