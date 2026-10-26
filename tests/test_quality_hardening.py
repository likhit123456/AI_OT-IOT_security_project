"""Regression tests for combined hazards, cancellation races, and evidence lifetime."""
import json
import threading

import pytest
from fastapi import HTTPException
from test_sim_app_bugs import FakeSim, make_plant

from contracts import ControlCommand
from l0_plant.provenance_gate import CommandSigner, ProvenanceGate
from l0_plant.safety import SafeHold, SafetyInterlock
from monitor.observer import Observer, Verdict
from sim_app import ai


@pytest.mark.parametrize("first", [(2700, 4), (3001, 45)])
def test_combined_pressure_and_low_inventory_relieves_without_draining(first):
    sis = SafetyInterlock(safe_hold=SafeHold())
    sis.evaluate(*first)
    original = sis.cause
    sis.evaluate(3100, 4)
    inputs = sis.safe_state_inputs(3100, 4)
    assert sis.cause == original and sis.escalated
    assert inputs == {"e_stop": 0, "f1_valve_sp": 0, "f2_valve_sp": 0,
                      "purge_valve_sp": 100, "product_valve_sp": 0}
    sis.evaluate(2700, 50)
    assert sis.safe_state_inputs(2700, 50) == inputs
    assert sis.reset(2700, 50)
    assert not sis.escalated


@pytest.mark.parametrize("first", [(2700, 4), (3001, 45), (2700, 96)])
def test_sensor_loss_after_any_trip_escalates_and_latches(first):
    sis = SafetyInterlock(safe_hold=SafeHold())
    sis.evaluate(*first)
    sis.evaluate(float("nan"), 45)
    assert sis.safe_state_inputs() == {"e_stop": 1}
    sis.evaluate(2700, 50)
    assert sis.safe_state_inputs(2700, 50) == {"e_stop": 1}


def test_subprecision_tampering_is_rejected():
    from dataclasses import replace
    signed = CommandSigner(b"test").sign(ControlCommand(20, 20, 20, 20))
    assert not ProvenanceGate(b"test").admit(replace(signed, f1_valve=20.00001))[0]


def test_observer_discards_inflight_result_after_reset():
    entered, release = threading.Event(), threading.Event()
    def post(*_):
        entered.set()
        assert release.wait(3)
        return {"message": {"content": json.dumps({"label": "rationale_mismatch", "score": .9, "reason": "old"})}}
    obs = Observer(http_post=post)
    obs._gate.acquire()
    worker = threading.Thread(target=obs._run, args=(10, "proposals", "{}", obs._generation))
    worker.start()
    try:
        assert entered.wait(2)
        obs.reset()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and obs.last == {} and obs.suspicion() == 0
    assert obs._gate.acquire(blocking=False)


def test_observer_suspicion_expires_even_without_a_new_model_result():
    obs = Observer()
    obs.last["proposals"] = Verdict(10, "proposals", "rationale_mismatch", .9, "r")
    obs._last_feed_t = 11
    assert obs.suspicion() == .9
    obs._last_feed_t = 50
    assert obs.suspicion() == 0


def test_estop_during_inference_prevents_all_later_ai_writes(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    class SlowOperator:
        model = "test"
        def __init__(self, **_): self.log = []
        def decide(self, _):
            from contracts import SupervisoryTargets
            entered.set()
            assert release.wait(3)
            return SupervisoryTargets(pressure_sp=2600, level_sp=44, production_rate_sp=70)
    monkeypatch.setattr(ai, "OllamaOperator", SlowOperator)
    plant = make_plant(FakeSim(2900))
    plant.rescue.start()
    assert entered.wait(2)
    assert plant.manipulate("e_stop", None, 1)["value"] is True
    assert not plant.rescue.active and plant._client.e_stop
    release.set()
    for worker in threading.enumerate():
        if worker.name.startswith("ai-rescue-"):
            worker.join(3)
    assert plant._client.e_stop and plant._client.v == {"f1": 0, "f2": 0, "purge": 100, "product": 100}
    with pytest.raises(HTTPException, match="E-STOP"):
        plant.apply_valves({"f1": 100}, source="ai")
    assert plant.manipulate("e_stop", None, 0)["value"] is False


def test_emergency_stop_serializes_with_a_pending_valve_write():
    plant = make_plant()
    plant.rescue.active = True
    stop = threading.Event()
    run = plant.rescue._run_id
    plant.manipulate("e_stop", None, 1)
    assert not plant.rescue._apply(run, stop, {"f1": 100})
    assert plant._client.e_stop


def test_polling_start_is_idempotent_and_shutdown_joins_worker(monkeypatch):
    plant = make_plant()
    monkeypatch.setattr(plant, "read", FakeSim().read)
    plant.start()
    thread = plant._poll_thread
    assert plant.start()._poll_thread is thread
    plant.stop()
    assert not thread.is_alive() and not plant.connected


@pytest.mark.parametrize("source", ["manual", "ai"])
def test_direct_nonfinite_batch_write_is_rejected(source):
    plant = make_plant()
    with pytest.raises(HTTPException) as exc:
        plant.apply_valves({"f1": float("nan")}, source)
    assert exc.value.status_code == 422


def test_action_flood_is_bounded_without_blocking_control_thread():
    from live.engine import Engine
    engine = Engine(reset=False)
    for _ in range(64):
        assert engine.submit("forged") == "queued"
    with pytest.raises(ValueError, match="queue is full"):
        engine.submit("forged")
    assert engine._q.qsize() == 64


def test_snapshot_marks_old_measurements_stale():
    from live.engine import Engine
    engine = Engine(reset=False)
    engine.status = "running"
    engine.snapshot_data = {"t": 1, "status": "running"}
    engine.now = lambda: 7
    assert engine.read()["telemetry_stale"]
    assert engine.read()["telemetry_age_s"] == 6


def test_failed_emergency_stop_is_pending_then_reasserted_on_reconnect(monkeypatch):
    acknowledged = threading.Event()
    class Sim(FakeSim):
        def write_inputs(self, inputs):
            result = super().write_inputs(inputs)
            if inputs.get("e_stop"):
                acknowledged.set()
            return result
    plant = make_plant(Sim())
    write = plant.write
    def disconnected(_):
        raise ConnectionError("temporary loss")
    monkeypatch.setattr(plant, "write", disconnected)
    with pytest.raises(HTTPException) as error:
        plant.manipulate("e_stop", None, 1)
    assert error.value.status_code == 503 and plant.snapshot()["e_stop_pending"]
    monkeypatch.setattr(plant, "write", write)
    plant.start()
    try:
        assert acknowledged.wait(2)
        # The data snapshot follows the same control transaction as the acknowledgement.
        with plant._control:
            assert not plant.snapshot()["e_stop_pending"]
    finally:
        plant.stop()
