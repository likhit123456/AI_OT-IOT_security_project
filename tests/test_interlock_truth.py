"""
Integration test: the SCRAM interlock trips off the independent
safety-instrumented sensor path even while a fault holds the basic control
system's MEASURED view in-band.

Requires the plant container to be running and reachable on 127.0.0.1:55555
(docker compose up -d simulation). Skipped automatically if it isn't.

Scenario:
  1. Freeze the pressure sensor (fault_injection.freeze) - its measured
     reading stops moving at a healthy, in-band value.
  2. Pin the feed valves open and the outflow valves shut via the normal L0
     control path (plant.apply) to drive TRUE pressure toward the hard limit,
     while confirming measured pressure never leaves its frozen band.
  3. Assert the interlock still trips (on truth), latches, forces the plant
     safe regardless of the commanded valves, and refuses reset_interlock()
     until truth is back inside the re-arm band - all while the frozen sensor
     is still reporting a healthy, in-band value throughout.
"""

from __future__ import annotations

import socket
import time

import pytest

from contracts import ControlCommand
from l0_plant.plant_service import PlantService
from l0_plant import fault_injection as fi
from scripts.reset_plant import reset_and_settle

HOST, PORT = "127.0.0.1", 55555


def _plant_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _plant_reachable(),
    reason=f"plant not reachable on {HOST}:{PORT} (docker compose up -d simulation)",
)


@pytest.fixture
def plant():
    # A previous run can leave valves pinned open (or any other mid-transient
    # position) on the container, which silently changes how long this test's
    # drive-to-trip takes. Reset to the designed operating point first so the
    # test is reproducible regardless of what ran before it.
    reset_and_settle()
    svc = PlantService(HOST, PORT).start()
    fi.clear_all(svc.client)
    yield svc
    # best-effort cleanup so one test run doesn't poison the next
    try:
        fi.clear_all(svc.client)
        if svc.safety.tripped:
            svc.reset_interlock()
        svc.client.write_inputs({"e_stop": 0})
    finally:
        svc.stop()


DRIVE_OPEN = ControlCommand(f1_valve=100.0, f2_valve=100.0,
                             purge_valve=0.0, product_valve=0.0)

TRIP_TIMEOUT_S = 240
REARM_TIMEOUT_S = 180
POLL_S = 1.0
MEASURED_BAND_KPA = 5.0  # frozen sensor must not drift beyond this


def test_interlock_trips_on_truth_while_measured_stays_in_band(plant: PlantService):
    limits = plant.safety.limits

    baseline = plant.read()
    assert not baseline.scrammed, "interlock already tripped before the test started"

    # Freeze the pressure sensor: measured holds at its current (healthy) value.
    fi.freeze(plant.client, "tank_pressure")
    frozen_measured = plant.read().pressure

    # Drive truth toward the trip limit via the real control path (plant.apply),
    # polling until the interlock reports tripped - or we time out.
    state = plant.read()
    deadline = time.monotonic() + TRIP_TIMEOUT_S
    measured_samples = []
    while not state.scrammed:
        state = plant.apply(DRIVE_OPEN)
        measured_samples.append(state.pressure)
        assert abs(state.pressure - frozen_measured) <= MEASURED_BAND_KPA, (
            f"measured pressure drifted out of band while frozen: "
            f"{state.pressure:.1f} vs frozen {frozen_measured:.1f}"
        )
        if time.monotonic() > deadline:
            pytest.fail(
                f"interlock never tripped within {TRIP_TIMEOUT_S}s; "
                f"last truth pressure={state.truth['pressure']:.1f} kPa, "
                f"measured stayed at ~{frozen_measured:.1f} kPa"
            )
        time.sleep(POLL_S)

    # It tripped - confirm it was truth that crossed the limit, not measured.
    assert state.truth["pressure"] >= limits.pressure_high
    assert state.pressure < limits.pressure_high, (
        "measured pressure also crossed the limit - the fault isn't masking anything, "
        "this run doesn't actually test the independent channel"
    )
    assert "pressure" in state.scram_reason.lower()
    assert plant.safety.tripped

    # Latching + override-proof: command the valves wide open again. The
    # interlock must force the plant safe regardless of what L1/L3 ask for.
    forced = plant.apply(DRIVE_OPEN)
    assert forced.scrammed
    assert forced.f1_valve_pos == pytest.approx(0.0, abs=1.0)
    assert forced.f2_valve_pos == pytest.approx(0.0, abs=1.0)
    assert forced.purge_valve_pos == pytest.approx(100.0, abs=1.0)
    assert forced.product_valve_pos == pytest.approx(100.0, abs=1.0)

    # Refuses to clear while truth is still outside the re-arm band.
    assert not plant.reset_interlock()
    assert plant.safety.tripped

    # Keep forcing safe (feeds shut, relief open) and wait for truth to fall
    # back inside the re-arm band.
    deadline = time.monotonic() + REARM_TIMEOUT_S
    rearmed_ok = False
    while time.monotonic() < deadline:
        s = plant.apply(DRIVE_OPEN)
        if plant.safety.can_reset(s.truth["pressure"], s.truth["liquid_level"]):
            rearmed_ok = True
            break
        time.sleep(POLL_S)
    assert rearmed_ok, f"truth pressure never fell back inside the re-arm band within {REARM_TIMEOUT_S}s"

    # Now the deliberate reset succeeds.
    assert plant.reset_interlock()
    assert not plant.safety.tripped

    # And measured pressure was in-band this whole time, including during the
    # trip and the force-safe window - the fault never had to "give up".
    assert max(measured_samples) - min(measured_samples) <= MEASURED_BAND_KPA


def test_reset_interlock_rejected_when_not_tripped_is_noop(plant: PlantService):
    # Sanity check on the untripped path: reset on a healthy plant is a no-op,
    # not an accidental trip-and-clear.
    assert not plant.safety.tripped
    assert not plant.reset_interlock()
    assert not plant.safety.tripped
