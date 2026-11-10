"""Fast pure-logic tests for enforcer/keel.py (no container, no network)."""
from __future__ import annotations

import math
import random

from shared.contracts import ControlCommand, PlantState
from enforcer.keel import EventLog, Keel, KeelLimits, SisFrame

KEY = b"test-key"
NOMINAL = ControlCommand(f1_valve=31.45, f2_valve=13.24, purge_valve=0.0, product_valve=47.03)
DT = 0.5


def state(p_meas=2700.0, l_meas=44.18, p_true=2700.0, l_true=44.18) -> PlantState:
    return PlantState(pressure=p_meas, level=l_meas, f1_flow=0, f2_flow=0, purge_flow=0,
                      product_flow=0, a_in_purge=0.47, b_in_purge=0, c_in_purge=0,
                      f1_valve_pos=31.45, f2_valve_pos=13.24, purge_valve_pos=0.0,
                      product_valve_pos=47.03, cost=0.0,
                      truth={"pressure": p_true, "liquid_level": l_true})


def run(keel, seconds, p_true, l_true, p_meas=None, l_meas=None, suspicion=0.0, t0=0.0):
    """Drive Keel with callables of t. Returns list of (t, Decision)."""
    out, t = [], t0
    while t < t0 + seconds:
        pt, lt = p_true(t), l_true(t)
        st = state(p_meas(t) if p_meas else pt, l_meas(t) if l_meas else lt, pt, lt)
        out.append((t, keel.step(NOMINAL, st, SisFrame.from_state(st), t, suspicion)))
        t += DT
    return out


def test_nominal_with_noise_never_intervenes():
    rnd = random.Random(1)
    k = Keel(KEY)
    res = run(k, 600, lambda t: 2700 + rnd.uniform(-1, 1), lambda t: 44.18 + rnd.uniform(-.02, .02))
    assert all(d.verdict == "allow" and not d.reasons for _, d in res)
    assert k.authority() == 1.0


def test_pressure_ramp_is_clamped_before_the_sis_trip():
    k = Keel(KEY)
    res = run(k, 100, lambda t: 2700 + 3.0 * t, lambda t: 44.18)        # SIS (3000) would trip at t=100
    first = next(t for t, d in res if d.verdict == "clamp" and d.severity > 0)
    assert first <= 60, first
    # moved toward safe only: feeds never raised, purge never lowered
    for _, d in res:
        assert d.command.f1_valve <= NOMINAL.f1_valve + 1e-9
        assert d.command.f2_valve <= NOMINAL.f2_valve + 1e-9
        assert d.command.purge_valve >= NOMINAL.purge_valve - 1e-9
    assert res[-1][1].command.purge_valve > 50
    assert k.authority() < 1.0


def test_slow_level_drain_trips_load_line_before_sis():
    k = Keel(KEY)
    # the repo's measured physics cap: ~0.013 %/s drain; demo SIS low-level trip at 40%
    res = run(k, 330, lambda t: 2700.0, lambda t: 44.18 - 0.013 * t)
    ll = next(t for t, d in res if any(r.code == "load_line" and r.hazard == "level_low" for r in d.reasons))
    assert ll < 250, ll
    sis_trip_t = (44.18 - 40.0) / 0.013                                  # ~321 s
    assert ll < sis_trip_t - 60


def test_divergence_from_spoofed_measurement_costs_authority():
    k = Keel(KEY)
    # attacker freezes the measured pressure; truth creeps up too slowly for the rate bound
    res = run(k, 120, lambda t: 2700 + 1.0 * t, lambda t: 44.18, p_meas=lambda t: 2700.0)
    assert any(r.code == "divergence" for _, d in res for r in d.reasons)
    assert k.authority() < 1.0


def test_measured_view_can_never_relax_a_clamp():
    # measured says "all fine" while the SIS channel is in the soft zone
    k = Keel(KEY)
    st = state(p_meas=2600.0, p_true=2990.0)
    d = k.step(NOMINAL, st, SisFrame.from_state(st), 0.0)
    assert d.severity > 0.9 and d.command.purge_valve > 90


def test_safety_moves_are_not_throttled():
    k = Keel(KEY)
    st = state(p_true=2995.0)
    k.step(NOMINAL, st, SisFrame.from_state(st), 0.0)
    d = k.step(NOMINAL, st, SisFrame.from_state(st), 0.5)
    assert d.command.purge_valve > 90          # would be <= 2.5% if throttled by slew


def test_l2_alone_cannot_push_authority_below_floor():
    k = Keel(KEY)
    run(k, 2000, lambda t: 2700.0, lambda t: 44.18, suspicion=1.0)
    assert k.authority() >= KeelLimits().l2_floor - 1e-9
    assert k.authority() < 1.0                 # but it does have some effect


def test_authority_recovers_slowly_when_clean():
    k = Keel(KEY)
    for i in range(5):
        k.note_gate_rejection(float(i))
    low = k.authority()
    run(k, 200, lambda t: 2700.0, lambda t: 44.18, t0=10.0)
    assert k.authority() > low + 0.5


def test_gate_rejections_lead_to_divert():
    k = Keel(KEY)
    for i in range(6):
        k.note_gate_rejection(float(i))
    res = run(k, 25, lambda t: 2700.0, lambda t: 44.18, t0=6.0)
    assert any(d.verdict == "divert" for _, d in res)


def test_invalid_sis_reading_fails_safe():
    k = Keel(KEY)
    st = state()
    d = k.step(NOMINAL, st, SisFrame(math.nan, 44.0), 0.0)
    assert d.severity == 1.0 and d.command.purge_valve == 100.0


def test_event_log_chain_verifies_and_detects_tamper():
    k = Keel(KEY)
    run(k, 100, lambda t: 2700 + 3.0 * t, lambda t: 44.18)
    evs = list(k.log.events)
    assert len(evs) > 3 and EventLog.verify(KEY, evs)
    evs[2] = {**evs[2], "detail": {"verdict": "allow"}}
    assert not EventLog.verify(KEY, evs)
    assert not EventLog.verify(b"wrong", list(k.log.events))
