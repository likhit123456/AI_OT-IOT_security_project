"""
Research-only plant wrapper for the red-team evaluation harness
(experiments/adversarial_eval.py).

This is NOT the production L0 service. l0_plant/plant_service.py always
enforces its interlock and has no bypass - that's intentional and this file
does not change it. EvalPlant exists only so the evaluation can run its two
counterfactual baseline conditions ("no enforcer" and "behavior-monitor-only")
without a protective action in the loop, while still privately running the
exact same SIS-grounded trip logic (l0_plant.safety.SafetyInterlock, evaluated
against outputs/truth, never measured) every cycle for shadow scoring - so
even the un-enforced runs can report "the real interlock would have tripped
here." enforce=True reproduces PlantService.apply's behavior exactly (same
class, same force-safe-via-e_stop action).
"""

from __future__ import annotations

from typing import Optional

from contracts import PlantState, ControlCommand
from l0_plant.grfics_client import GrficsClient
from l0_plant.safety import SafetyInterlock, TripLimits


class EvalPlant:
    def __init__(self, host: str = "127.0.0.1", port: int = 55555,
                 limits: Optional[TripLimits] = None, enforce: bool = True):
        self.client = GrficsClient(host, port)
        self.safety = SafetyInterlock(limits)
        self.enforce = enforce

    def start(self) -> "EvalPlant":
        self.client.connect()
        return self

    def stop(self) -> None:
        self.client.close()

    def _to_state(self, raw: dict) -> PlantState:
        m = raw.get("measured", {})
        s = raw.get("state", {})
        o = raw.get("outputs", {})
        # Always evaluate the real trip logic on truth, enforced or not - this
        # is the shadow score ("would the SIS have tripped by now").
        would_trip = self.safety.evaluate(pressure=o.get("pressure", 0.0),
                                           level=o.get("liquid_level", 0.0))
        scrammed = would_trip if self.enforce else False
        return PlantState(
            pressure=m.get("pressure", 0.0),
            level=m.get("liquid_level", 0.0),
            f1_flow=m.get("f1_flow", 0.0),
            f2_flow=m.get("f2_flow", 0.0),
            purge_flow=m.get("purge_flow", 0.0),
            product_flow=m.get("product_flow", 0.0),
            a_in_purge=m.get("A_in_purge", 0.0),
            b_in_purge=m.get("B_in_purge", 0.0),
            c_in_purge=m.get("C_in_purge", 0.0),
            f1_valve_pos=s.get("f1_valve_pos", 0.0),
            f2_valve_pos=s.get("f2_valve_pos", 0.0),
            purge_valve_pos=s.get("purge_valve_pos", 0.0),
            product_valve_pos=s.get("product_valve_pos", 0.0),
            cost=o.get("cost", 0.0),
            scrammed=scrammed,
            scram_reason=self.safety.reason if would_trip else "",
            truth=o or None,
        )

    def read(self) -> PlantState:
        return self._to_state(self.client.read())

    def apply(self, command: ControlCommand) -> PlantState:
        cmd = command.clamped()
        if self.enforce and self.safety.tripped:
            # Force-safe, cause-specific (SafetyInterlock.safe_state_inputs):
            # e_stop for pressure/high-level, product closed for low level.
            raw = self.client.write_inputs(self.safety.safe_state_inputs())
        else:
            raw = self.client.write_inputs({
                "f1_valve_sp": cmd.f1_valve,
                "f2_valve_sp": cmd.f2_valve,
                "purge_valve_sp": cmd.purge_valve,
                "product_valve_sp": cmd.product_valve,
                "e_stop": 0,
            })
        return self._to_state(raw)

    def reset_interlock(self) -> bool:
        raw = self.client.read()
        o = raw.get("outputs", {})
        return self.safety.reset(o.get("pressure", 0.0), o.get("liquid_level", 0.0))
