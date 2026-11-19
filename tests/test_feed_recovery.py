"""Accelerated mass-balance regression using the TE model's process equations.

This fixture advances process time without a live socket or wall-clock delays.
It exercises actual L0/Keel control; it is not a live-container validation.
"""
import math

import pytest

from contracts import ControlCommand
from enforcer.keel import Keel, KeelLimits, SisFrame
from l0_plant.plant_service import PlantService
from l0_plant.safety import DESIGN_VALVES, SafeHold
from live.engine import DEMO_LIMITS


class MassBalancePlant:
    def __init__(self):
        self.valves = dict(DESIGN_VALVES)
        self.valves["purge"] = 0.3926
        self.d = 44.18 / 100 * 30 * 8.3
        total = 2700 * (122 - self.d / 8.3) / (8.314 * 373)
        c_fraction = (100 / (0.00117 * (2700 * .47) ** 1.2)) ** (1 / .4) / 2700
        self.a, self.b, self.c = .47 * total, (1 - .47 - c_fraction) * total, c_fraction * total

    def read(self):
        total = self.a + self.b + self.c
        pressure = total * 8.314 * 373 / (122 - self.d / 8.3)
        level = self.d / 8.3 / 30 * 100
        root = math.sqrt(max(0, pressure - 100))
        outputs = {"pressure": pressure, "liquid_level": level,
                   "f1_flow": 6.4046 * self.valves["f1"], "f2_flow": .4246 * self.valves["f2"],
                   "purge_flow": .352 * self.valves["purge"] * root,
                   "product_flow": .0417 * self.valves["product"] * root if self.d > 0 else 0,
                   "A_in_purge": self.a / total, "B_in_purge": self.b / total, "C_in_purge": self.c / total}
        return {"outputs": outputs, "measured": dict(outputs),
                "state": {f"{key}_valve_pos": value for key, value in self.valves.items()}}

    def write_inputs(self, inputs):
        for valve in self.valves:
            self.valves[valve] = inputs.get(f"{valve}_valve_sp", self.valves[valve])
        if inputs.get("e_stop"):
            self.valves.update(f1=0, f2=0, purge=100, product=100)
        before = self.read()["outputs"]
        reaction = .00117 * (before["pressure"] * before["A_in_purge"]) ** 1.2 * (before["pressure"] * before["C_in_purge"]) ** .4
        f1, f2, purge, product = (before[key] for key in ("f1_flow", "f2_flow", "purge_flow", "product_flow"))
        dt = 1 / 3600  # one simulated second
        self.a = max(0, self.a + (.485 * f1 + f2 - before["A_in_purge"] * purge - reaction) * dt)
        self.b = max(0, self.b + (.005 * f1 - before["B_in_purge"] * purge) * dt)
        self.c = max(0, self.c + (.510 * f1 - before["C_in_purge"] * purge - reaction) * dt)
        self.d = max(0, self.d + (reaction - product) * dt)
        return self.read()


@pytest.mark.parametrize("bypass_keel", [False, True])
def test_zero_feeds_recover_then_accept_another_attack(bypass_keel):
    plant = MassBalancePlant()
    service = PlantService(limits=DEMO_LIMITS, safe_hold=SafeHold())
    service.client = plant
    keel = Keel(b"test", KeelLimits(trip=DEMO_LIMITS))
    stopped = ControlCommand(0, 0, .3926, 47.03)
    seen_trip = False
    min_pressure, min_level = 2700, 44.18
    state = service.read()
    for t in range(1800):
        command = stopped if bypass_keel else keel.step(stopped, state, SisFrame.from_state(state), t).command
        state = service.apply(command)
        min_pressure = min(min_pressure, state.truth["pressure"])
        min_level = min(min_level, state.truth["liquid_level"])
        seen_trip |= state.scrammed
        # A user clears the bad request once protection is active, and re-arms
        # only when L0 confirms recovery on its own channel.
        if seen_trip and service.safety.can_reset(state.truth["pressure"], state.truth["liquid_level"]):
            assert service.reset_interlock()
            break
    assert seen_trip if bypass_keel else any(reason.hazard == "pressure_low" for reason in keel.last.reasons)
    assert min_pressure > 2000 and min_level > 38
    assert not service.safety.tripped, (state.truth, service.safety.cause)
    # Another pressure attack must still trigger protection after recovery.
    attack = ControlCommand(100, 100, 0, 0)
    for _ in range(1800):
        state = service.apply(attack)
        if state.scrammed:
            state = service.apply(attack)
            break
    assert service.safety.tripped and service.safety.cause == "pressure_high"
    assert plant.valves["f1"] < 100 and plant.valves["purge"] > 0
    assert all(math.isfinite(value) for value in state.truth.values())
