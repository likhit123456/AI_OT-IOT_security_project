"""Fast tests for l1_regulator.L1Regulator against a toy TE-like plant (no container).

The toy plant only has the right SIGNS and rough magnitudes of the live plant
(pressure ~0.13 kPa/s per % purge and ~0.035 kPa/s per % of total feed, with a
5 s lag; level integrates product inflow minus outflow; A responds to feed 2).
It exists to check loop structure - tracking, no sustained oscillation, no
windup, units, bumpless transfer - not to tune gains (that is done live).
"""
from __future__ import annotations

from dataclasses import replace

from contracts import ControlCommand, PlantState, SupervisoryTargets
from l0_plant.provenance_gate import ProvenanceGate
from l1_regulator import (DESIGN_F1, DESIGN_F2, DESIGN_PRODUCT, DESIGN_PURGE, L1Regulator)

KEY = b"l1-test-key"
DESIGN = dict(pressure_sp=2700.6, level_sp=44.18, production_rate_sp=100.0, a_comp_sp=47.0)
DT = 0.1


class ToyPlant:
    def __init__(self):
        self.p, self.p_drive, self.l, self.q, self.a = 2700.6, 0.0, 44.18, 100.0, 0.47
        self.v = ControlCommand(DESIGN_F1, DESIGN_F2, DESIGN_PURGE, DESIGN_PRODUCT)

    def state(self) -> PlantState:
        return PlantState(pressure=self.p, level=self.l, f1_flow=0, f2_flow=0, purge_flow=0,
                          product_flow=self.q, a_in_purge=self.a, b_in_purge=0, c_in_purge=0,
                          f1_valve_pos=self.v.f1_valve, f2_valve_pos=self.v.f2_valve,
                          purge_valve_pos=self.v.purge_valve, product_valve_pos=self.v.product_valve, cost=0.0)

    def step(self, cmd: ControlCommand, dt: float = DT) -> None:
        self.v = cmd
        feed = (cmd.f1_valve + cmd.f2_valve) - (DESIGN_F1 + DESIGN_F2)
        drive = 0.035 * feed - 0.13 * (cmd.purge_valve - DESIGN_PURGE)
        self.p_drive += (drive - self.p_drive) * dt / 5.0                 # 5 s lag
        self.p += self.p_drive * dt
        inflow = 100.0 * cmd.f1_valve / DESIGN_F1
        outflow = 100.0 * cmd.product_valve / DESIGN_PRODUCT
        self.l += 0.004 * (inflow - outflow) * dt
        self.q += (outflow - self.q) * dt / 3.0
        self.a += 0.0004 * ((cmd.f2_valve - DESIGN_F2) - 20.0 * (self.a - 0.47)) * dt


def run(l1, plant, sp, seconds, trace=None):
    for _ in range(int(seconds / DT)):
        cmd = l1.regulate(SupervisoryTargets(**sp), plant.state(), DT)
        plant.step(cmd)
        if trace is not None:
            trace.append((plant.p, plant.l, plant.q, plant.a * 100, cmd))
    return plant


def fresh():
    plant = ToyPlant()
    l1 = L1Regulator(KEY)
    l1.bumpless(plant.state(), SupervisoryTargets(**DESIGN))
    return l1, plant


def test_holds_the_design_point():
    l1, plant = fresh()
    run(l1, plant, DESIGN, 300)
    assert abs(plant.p - 2700.6) < 1.0 and abs(plant.l - 44.18) < 0.05 and abs(plant.a - 0.47) < 0.001
    assert abs(plant.v.purge_valve - DESIGN_PURGE) < 1.0 and abs(plant.v.product_valve - DESIGN_PRODUCT) < 1.0


def _settles(xs, target, band, tail):
    """within band of target over the last `tail` samples, and no sustained oscillation
    (error sign changes over the tail are few)."""
    end = xs[-tail:]
    flips = sum((a - target) * (b - target) < 0 for a, b in zip(end, end[1:]))
    return all(abs(x - target) <= band for x in end) and flips <= 4


def test_each_setpoint_step_settles_without_sustained_oscillation():
    cases = [("pressure_sp", 2750.6, 0, 5.0), ("pressure_sp", 2650.6, 0, 5.0),
             ("level_sp", 46.0, 1, 0.2), ("production_rate_sp", 110.0, 2, 2.0), ("a_comp_sp", 48.0, 3, 0.2)]
    for key, val, idx, band in cases:
        l1, plant = fresh()
        run(l1, plant, DESIGN, 30)
        trace = []
        run(l1, plant, {**DESIGN, key: val}, 600, trace)
        xs = [t[idx] for t in trace]
        assert _settles(xs, val, band, tail=1500), (key, val, xs[-1])
        over = (max(xs) - val) if val > DESIGN[key] else (val - min(xs))
        assert over <= 3 * band, (key, over)


def test_no_windup_after_an_unreachable_setpoint():
    from l1_regulator import FEED_SPAN, FEED_TRIM_MAX
    l1, plant = fresh()
    run(l1, plant, {**DESIGN, "pressure_sp": 3500.0}, 300)      # feeds pinned at full trim
    assert abs(plant.v.f1_valve - DESIGN_F1 * (1 + FEED_TRIM_MAX)) < 0.5
    assert l1.pressure.i > -FEED_SPAN                          # integral frozen while saturated, not run off
    trace = []
    run(l1, plant, DESIGN, 400, trace)                         # hand back a reachable target
    off = next(i for i, t in enumerate(trace) if t[4].f1_valve < DESIGN_F1 * 1.01)
    assert off * DT < 5.0                                       # trim released at once, not unwound for minutes
    assert _settles([t[0] for t in trace], 2700.6, 5.0, tail=1000)


def test_a_comp_setpoint_and_measurement_are_in_the_same_units():
    l1, plant = fresh()
    run(l1, plant, {**DESIGN, "a_comp_sp": 47.5}, 600)
    assert abs(plant.a * 100.0 - 47.5) < 0.1                    # mol % target -> fraction 0.475 measured
    assert 0.0 < plant.v.f2_valve < 100.0                        # not saturated by a 100x unit error


def test_l1_commands_pass_the_gate_and_tampered_ones_do_not():
    l1, plant = fresh()
    gate = ProvenanceGate(KEY)
    for _ in range(50):
        cmd = l1.sign(l1.regulate(SupervisoryTargets(**DESIGN), plant.state(), DT))
        ok, why = gate.admit(cmd)
        assert ok, why
        plant.step(cmd)
    good = l1.sign(l1.regulate(SupervisoryTargets(**DESIGN), plant.state(), DT))
    assert not gate.admit(replace(good, purge_valve=good.purge_valve + 5.0))[0]   # tampered valve
    assert not gate.admit(replace(good, auth_tag=""))[0]                          # unsigned
    assert gate.admit(good)[0] and not gate.admit(good)[0]                        # replay rejected
    assert not ProvenanceGate(b"other-key").admit(l1.sign(good))[0]               # wrong key


def test_bumpless_on_rearm_and_on_authority_hand_back():
    l1, plant = fresh()
    plant.p, plant.v = 2790.0, ControlCommand(31.45, 13.24, 0.39, 47.03)          # re-arm far from setpoint
    l1.bumpless(plant.state(), SupervisoryTargets(**DESIGN))
    first = l1.regulate(SupervisoryTargets(**DESIGN), plant.state(), DT)
    assert abs(first.purge_valve - 0.39) < 0.05 and abs(first.product_valve - 47.03) < 0.05
    assert abs(first.f1_valve - 31.45) < 0.05 and abs(first.f2_valve - 13.24) < 0.05
    l1, plant = fresh()
    run(l1, plant, {**DESIGN, "pressure_sp": 2650.6}, 60)                         # purge working
    held = plant.v
    hold = SupervisoryTargets()                                                    # authority 0: all None
    for _ in range(50):
        c = l1.regulate(hold, plant.state(), DT)
        assert c.purge_valve == held.purge_valve and c.product_valve == held.product_valve
        plant.step(c)
    back = l1.regulate(SupervisoryTargets(**{**DESIGN, "pressure_sp": 2650.6}), plant.state(), DT)
    for v in ("f1_valve", "f2_valve", "purge_valve", "product_valve"):
        assert abs(getattr(back, v) - getattr(held, v)) < 0.5, v                    # no kick on hand-back
