"""
Demo: the L3 governance authority handshake (l3_brain/operator.py) against
the live plant container, at authority 1.0 / 0.5 / 0.0.

Takes one real PlantState snapshot from the running container, then runs
AIOperator.step() against it with three fixed authority_fn values to show the
targets getting progressively more conservative - full request, blended
toward current, then a true hold (all setpoints None). Does not drive the
plant; read-only.

Prereq: docker compose up -d simulation
"""

from __future__ import annotations

from l0_plant.plant_service import PlantService
from l3_brain.operator import AIOperator


def show(authority: float, state) -> None:
    op = AIOperator(authority_fn=lambda a=authority: a)
    targets = op.step(state)
    print(f"authority={authority:.1f}")
    print(f"  pressure_sp        = {targets.pressure_sp}")
    print(f"  level_sp           = {targets.level_sp}")
    print(f"  production_rate_sp = {targets.production_rate_sp}")
    print(f"  a_comp_sp          = {targets.a_comp_sp}")
    print(f"  authority (echoed) = {targets.authority:.2f}")
    print(f"  rationale          = {targets.rationale}")
    print()


def main() -> None:
    plant = PlantService().start()
    try:
        state = plant.read()
        print(f"snapshot: pressure={state.pressure:.1f} kPa  level={state.level:.2f} %  "
              f"product_flow={state.product_flow:.2f}  a_in_purge={state.a_in_purge:.4f}\n")
        for a in (1.0, 0.5, 0.0):
            show(a, state)
    finally:
        plant.stop()


if __name__ == "__main__":
    main()
