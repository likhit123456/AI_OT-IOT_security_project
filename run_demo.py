"""
End-to-end SCRAM demo: L0 (yours) + L1 stub (teammate placeholder) + L3 (yours).

Wires one closed control cycle against the live plant container on :55555:

    L3 BaselineOperator.decide(state) -> SupervisoryTargets
          |
    L1ControlStub.regulate(targets, state, dt) -> ControlCommand
          |
    L0 PlantService.apply(command) -> next PlantState
          |  (back up to the top of the next cycle)

Demonstrates that:
  * L3 only ever emits setpoints (never valves),
  * L1 regulates to them (including the A_in_purge fraction/percent
    reconciliation - see l1_stub.py),
  * L0 publishes measured state and owns the independent interlock, which
    stays armed (evaluating every cycle) without tripping under baseline
    operation.

Prereq: the plant container must be running and reachable on :55555:
    docker compose up -d simulation
"""

from __future__ import annotations

import time

from l0_plant.plant_service import PlantService
from l3_brain.operator import AIOperator
from l1_stub import L1ControlStub
from scripts.reset_plant import reset_and_settle


def main(cycles: int = 200, dt: float = 0.5, sleep_s: float = 0.3, reset: bool = True):
    if reset:
        # Start from the designed operating point, not whatever valve
        # positions / transient a previous run or test left on the
        # container - otherwise this prints a recovery transient, not
        # baseline regulation. See scripts/reset_plant.py.
        point = reset_and_settle()
        print(f"[reset] settled at P={point.pressure:.1f} kPa  L={point.level:.2f} %  "
              f"(range {point.pressure_range:.2f} kPa / {point.level_range:.3f} % "
              f"over {point.cycles_to_settle} cycles)\n")
    plant = PlantService().start()
    brain = AIOperator()                 # full authority (no governance yet)
    l1 = L1ControlStub()

    pressures, levels = [], []

    header = (f"{'t':>4} {'P_kPa':>8} {'lvl%':>6} {'prod':>7} {'purge':>7} "
               f"{'A%':>6}  {'f1v':>5}{'f2v':>5}{'prgv':>6}{'prdv':>6}  "
               f"{'auth':>5}  {'armed':>6}  intent")
    print(header)
    print("-" * len(header))

    try:
        state = plant.read()
        for k in range(cycles):
            targets = brain.step(state)                 # L3: state -> intent
            command = l1.regulate(targets, state, dt)   # L1: intent -> valves
            state = plant.apply(command)                # L0: valves -> state

            pressures.append(state.pressure)
            levels.append(state.level)
            armed = "TRIP!" if state.scrammed else "armed"

            print(f"{k:>4} {state.pressure:8.1f} {state.level:6.1f} "
                  f"{state.product_flow:7.2f} {state.purge_flow:7.2f} "
                  f"{state.a_in_purge*100.0:6.2f}  "
                  f"{state.f1_valve_pos:5.1f}{state.f2_valve_pos:5.1f}"
                  f"{state.purge_valve_pos:6.1f}{state.product_valve_pos:6.1f}  "
                  f"{targets.authority:5.2f}  {armed:>6}  {targets.rationale}")
            time.sleep(sleep_s)
    finally:
        plant.stop()

    print("-" * len(header))
    print(f"{cycles} cycles complete.")
    print(f"  pressure: min={min(pressures):.1f}  max={max(pressures):.1f}  "
          f"mean={sum(pressures)/len(pressures):.1f} kPa  "
          f"(trip limit {plant.safety.limits.pressure_high:.0f} kPa)")
    print(f"  level:    min={min(levels):.1f}  max={max(levels):.1f}  "
          f"mean={sum(levels)/len(levels):.1f} %  "
          f"(trip band {plant.safety.limits.level_low:.0f}-{plant.safety.limits.level_high:.0f} %)")
    print(f"  interlock ever tripped: {plant.safety.tripped}")


if __name__ == "__main__":
    main()
