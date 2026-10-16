"""
Fast unit test for l0_plant/safety.py: pure logic, no container, no network.

Feeds SafetyInterlock.evaluate()/reset() plain pressure/level numbers and
checks trip, latch, override-while-tripped, and re-arm behavior. Should run
in well under a second - if it doesn't, something is talking to the network.
"""

from __future__ import annotations

import pytest

from l0_plant.safety import SafetyInterlock, TripLimits


def make_interlock() -> SafetyInterlock:
    return SafetyInterlock(TripLimits(
        pressure_high=3000.0, level_low=5.0, level_high=95.0,
        pressure_rearm=2800.0, level_low_rearm=15.0, level_high_rearm=85.0,
    ))


# ---- trip conditions -------------------------------------------------------

def test_no_trip_within_normal_envelope():
    sis = make_interlock()
    assert sis.evaluate(pressure=2700.0, level=50.0) is False
    assert not sis.tripped
    assert sis.reason == ""


def test_trips_on_high_pressure():
    sis = make_interlock()
    assert sis.evaluate(pressure=3000.0, level=50.0) is True
    assert sis.tripped
    assert "pressure" in sis.reason.lower()


def test_trips_on_low_level():
    sis = make_interlock()
    assert sis.evaluate(pressure=2700.0, level=5.0) is True
    assert sis.tripped
    assert "level" in sis.reason.lower()


def test_trips_on_high_level():
    sis = make_interlock()
    assert sis.evaluate(pressure=2700.0, level=95.0) is True
    assert sis.tripped
    assert "level" in sis.reason.lower()


def test_does_not_trip_just_below_each_limit():
    sis = make_interlock()
    assert sis.evaluate(pressure=2999.9, level=50.0) is False
    assert sis.evaluate(pressure=2700.0, level=5.1) is False
    assert sis.evaluate(pressure=2700.0, level=94.9) is False
    assert not sis.tripped


# ---- latching ---------------------------------------------------------------

def test_trip_latches_even_after_values_recover():
    sis = make_interlock()
    assert sis.evaluate(pressure=3100.0, level=50.0) is True
    # Values instantly back to nominal - interlock must stay tripped.
    assert sis.evaluate(pressure=2700.0, level=50.0) is True
    assert sis.tripped
    assert "pressure" in sis.reason.lower()  # original trip reason preserved


def test_latched_trip_ignores_a_second_different_excursion():
    # First excursion sets the reason; a later, different excursion while
    # still latched must not silently overwrite it or re-run trip logic.
    sis = make_interlock()
    sis.evaluate(pressure=3100.0, level=50.0)
    reason_after_first = sis.reason
    sis.evaluate(pressure=2700.0, level=2.0)  # also over a limit, but already tripped
    assert sis.tripped
    assert sis.reason == reason_after_first


# ---- override-proof: evaluate() ignores intent, only reads values ----------

def test_once_tripped_every_call_reports_tripped_regardless_of_values():
    sis = make_interlock()
    sis.evaluate(pressure=3100.0, level=50.0)
    for pressure, level in [(0.0, 50.0), (2700.0, 50.0), (3200.0, 0.0)]:
        assert sis.evaluate(pressure=pressure, level=level) is True
        assert sis.tripped


# ---- re-arm / reset ---------------------------------------------------------

def test_reset_refused_while_still_outside_rearm_band():
    sis = make_interlock()
    sis.evaluate(pressure=3100.0, level=50.0)
    assert sis.tripped
    # Below the trip limit but still above the (tighter) re-arm limit.
    assert sis.can_reset(pressure=2900.0, level=50.0) is False
    assert sis.reset(pressure=2900.0, level=50.0) is False
    assert sis.tripped  # still latched


def test_reset_refused_just_outside_rearm_band_on_level():
    sis = make_interlock()
    sis.evaluate(pressure=2700.0, level=95.0)
    assert sis.can_reset(pressure=2700.0, level=85.1) is False
    assert sis.reset(pressure=2700.0, level=85.1) is False
    assert sis.tripped


def test_reset_succeeds_once_inside_rearm_band():
    sis = make_interlock()
    sis.evaluate(pressure=3100.0, level=50.0)
    assert sis.can_reset(pressure=2800.0, level=50.0) is True
    assert sis.reset(pressure=2800.0, level=50.0) is True
    assert not sis.tripped
    assert sis.reason == ""


def test_reset_on_untripped_interlock_is_a_noop():
    sis = make_interlock()
    assert not sis.tripped
    assert sis.reset(pressure=2700.0, level=50.0) is False
    assert not sis.tripped


def test_can_trip_again_after_a_clean_reset():
    sis = make_interlock()
    sis.evaluate(pressure=3100.0, level=50.0)
    sis.reset(pressure=2700.0, level=50.0)
    assert not sis.tripped
    assert sis.evaluate(pressure=3050.0, level=50.0) is True
    assert sis.tripped


# ---- cause-specific safe state ---------------------------------------------

def test_low_level_trip_closes_product_and_does_not_estop():
    sis = make_interlock()
    sis.evaluate(pressure=2700.0, level=5.0)
    assert sis.cause == "level_low"
    inputs = sis.safe_state_inputs()
    assert inputs["product_valve_sp"] == 0.0
    assert inputs["e_stop"] == 0
    # feed/purge valves are deliberately not written (held)
    assert not any(k in inputs for k in ("f1_valve_sp", "f2_valve_sp", "purge_valve_sp"))


def test_pressure_and_high_level_trips_still_estop():
    for p, l in ((3100.0, 50.0), (2700.0, 95.0)):
        sis = make_interlock()
        sis.evaluate(pressure=p, level=l)
        assert sis.safe_state_inputs() == {"e_stop": 1}


def test_cause_latches_with_first_trip_and_clears_on_reset():
    sis = make_interlock()
    sis.evaluate(pressure=2700.0, level=2.0)
    sis.evaluate(pressure=3100.0, level=2.0)      # second hazard while latched
    assert sis.cause == "level_low"
    assert sis.reset(pressure=2700.0, level=50.0)
    assert sis.cause == ""


# ---- invalid safety-channel readings fail safe ---------------------------------



@pytest.mark.parametrize("p,lv", [(float("nan"), 50.0), (2700.0, float("nan")), (None, 50.0),
                                  (2700.0, None), (float("inf"), 50.0), ("2700", 50.0)])
def test_invalid_reading_trips_with_sensor_fault(p, lv):
    sis = SafetyInterlock()
    assert sis.evaluate(pressure=p, level=lv) is True
    assert sis.cause == "sis_sensor_fault"
    assert sis.safe_state_inputs() == {"e_stop": 1}


def test_cannot_rearm_on_invalid_reading():
    sis = SafetyInterlock()
    sis.evaluate(pressure=float("nan"), level=50.0)
    assert sis.can_reset(float("nan"), 50.0) is False
    assert sis.reset(None, 50.0) is False and sis.tripped
    assert sis.reset(2700.0, 50.0) is True and not sis.tripped
