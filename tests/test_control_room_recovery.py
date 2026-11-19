"""Recovery and operator contracts, without Docker, browsers, or model services."""
import io
import json
import math
from types import SimpleNamespace

import pytest

from contracts import ControlCommand
from enforcer.keel import Keel, SisFrame
from l0_plant.safety import DESIGN_VALVES, SafeHold, SafetyInterlock
from l3_brain.policy import OllamaOperator
from live import commands
from live.engine import Engine
from live.server import make_handler
from test_keel import state


@pytest.mark.parametrize("text,expected", [
    ("open both feeds to 100% and close the purge", {"f1": 100, "f2": 100, "purge": 0}),
    ("feed 1 to 20% and feed 2 to 30%", {"f1": 20, "f2": 30}),
    ("close both feeds and open the product fully", {"f1": 0, "f2": 0, "product": 100}),
    ("feed 1 and feed 2 to 0%", {"f1": 0, "f2": 0}),
    ("purge to 1.5%, product to 45%", {"purge": 1.5, "product": 45}),
])
def test_each_valve_clause_keeps_its_own_value(text, expected):
    assert commands.parse(text) == {"kind": "valves", "valves": expected}


def test_invalid_manual_valves_are_rejected_before_queueing():
    engine = Engine(reset=False)
    with pytest.raises(ValueError, match="0-100"):
        engine.submit("chat", text="feed 1 to 150%")
    assert engine._q.empty()


def test_low_pressure_guard_uses_only_the_independent_channel():
    zero_feed = ControlCommand(0, 0, 100, 100)
    physical_low = state(p_meas=2700, p_true=2350)
    decision = Keel(b"test").step(zero_feed, physical_low, SisFrame.from_state(physical_low), 0)
    assert decision.command.f1_valve == DESIGN_VALVES["f1"]
    assert decision.command.f2_valve == DESIGN_VALVES["f2"]
    assert decision.command.purge_valve == 0
    spoofed_low = state(p_meas=0, p_true=2700)
    decision = Keel(b"test").step(zero_feed, spoofed_low, SisFrame.from_state(spoofed_low), 0)
    assert decision.command.f1_valve == 0


def test_starvation_recovery_cannot_feed_an_overfull_vessel():
    overfull = state(p_true=2300, l_true=96)
    decision = Keel(b"test").step(ControlCommand(0, 0, 0, 100), overfull, SisFrame.from_state(overfull), 0)
    assert decision.command.f1_valve == decision.command.f2_valve == 0
    assert all(reason.hazard != "pressure_low" for reason in decision.reasons)


def test_low_pressure_and_inventory_clamps_apply_together():
    low = state(p_true=2350, l_true=1)
    decision = Keel(b"test").step(ControlCommand(0, 0, 100, 100), low, SisFrame.from_state(low), 0)
    assert decision.command.f1_valve > 0 and decision.command.product_valve == 0


@pytest.mark.parametrize("pressure,level,cause", [(2100, 45, "pressure_low"), (2700, 3, "level_low")])
def test_sis_restores_feed_instead_of_holding_an_attackers_zero_lineup(pressure, level, cause):
    sis = SafetyInterlock(safe_hold=SafeHold())
    assert sis.evaluate(pressure, level) and sis.cause == cause
    inputs = sis.safe_state_inputs(pressure, level)
    assert inputs["f1_valve_sp"] == DESIGN_VALVES["f1"]
    assert inputs["f2_valve_sp"] == DESIGN_VALVES["f2"]
    assert inputs["product_valve_sp"] == 0 and inputs["e_stop"] == 0
    assert not sis.can_reset(2100, 45)
    assert sis.can_reset(2700, 45)


def test_pressure_recovery_never_overrides_a_later_high_pressure_trip():
    sis = SafetyInterlock(safe_hold=SafeHold())
    sis.evaluate(2100, 45)
    sis.evaluate(3100, 45)
    assert sis.escalated and sis.safe_state_inputs(3100, 45) == {"e_stop": 1}


def test_baseline_setpoint_controls_work_and_clear_restores_design():
    engine = Engine(reset=False)
    engine._make_brain("baseline")
    engine.state = state()
    engine._act_chat("lower pressure to 2620 kPa and raise production by 10")
    targets = engine.policy.decide(engine.state)
    assert targets.pressure_sp == 2620 and targets.production_rate_sp == 110
    assert engine.policy.note
    engine._act_clear_note()
    assert engine.policy.note is None
    assert engine.policy.decide(engine.state).pressure_sp > 2700


def test_missing_model_does_not_reset_the_current_brain(monkeypatch):
    engine = Engine(reset=False)
    engine._make_brain("baseline")
    previous = engine.policy
    monkeypatch.setattr("live.engine.ollama_available", lambda: False)
    with pytest.raises(ValueError, match="unavailable"):
        engine.submit("brain", kind="ollama")
    assert engine.policy is previous and engine._q.empty()


def test_successive_baseline_relative_requests_use_the_latest_target():
    engine = Engine(reset=False)
    engine._make_brain("baseline")
    engine.state = state()
    engine._act_chat("raise production by 10")
    engine._act_chat("raise production by 10")
    assert engine.policy.production_target == 120


def test_actuator_demonstrations_keep_independent_schedules_and_can_be_replaced():
    writes = []
    engine = Engine(reset=False)
    engine.svc = SimpleNamespace(client=SimpleNamespace(write_inputs=writes.append))
    engine.now = lambda: 0
    engine._act_actuator_fault("f1", "demo")
    engine._act_actuator_fault("product", "demo")
    assert len(engine.scenarios) == 2
    engine._act_actuator_fault("f1", "cv")
    assert "valve_stick_f1" not in engine.scenarios
    assert writes[-1] == {"f1_stuck": 0, "f1_cv_scale": .5}
    engine._advance_actuator_faults(21)
    assert writes[-1] == {"product_stuck": 1}
    assert not engine.scenarios


@pytest.mark.parametrize("action,kwargs", [
    ("forged", {"duration_s": math.nan}),
    ("operator_injection", {"duration_s": 0}),
    ("actuator_fault", {"valve": "invalid"}),
    ("brain", {"kind": "invalid"}),
])
def test_invalid_attack_and_brain_inputs_fail_synchronously(action, kwargs):
    engine = Engine(reset=False)
    with pytest.raises(ValueError):
        engine.submit(action, **kwargs)
    assert engine._q.empty()


def test_clear_faults_removes_all_scenarios_and_cached_ai_intent():
    engine = Engine(reset=False)
    engine.policy = OllamaOperator()
    engine.policy.set_note("raise pressure")
    generation = engine.policy._generation
    engine.svc = SimpleNamespace(client=SimpleNamespace(write_inputs=lambda _: None))
    engine.scenarios = {"forged": {}, "operator_injection": {}, "valve_stick": {}, "stolen_key": {"until": 10}}
    engine.faults = {"tank_level": "drift"}
    engine._act_clear_faults()
    assert not engine.scenarios and not engine.faults and not engine.policy.note
    assert engine.policy._generation > generation
    assert engine.policy._cached.pressure_sp is None


def test_reset_changes_run_identity_and_cancels_old_queued_attacks():
    engine = Engine(reset=False)
    engine._make_brain("baseline")
    engine.svc = SimpleNamespace(stop=lambda: None)
    engine._connect = lambda **_: None
    engine.history.append([1, 1])
    engine.decisions.append({"old": True})
    engine._auto_rearms.extend([1, 2, 3])
    previous_run = engine.run_id
    engine.submit("reset_plant")
    engine.submit("forged")
    while not engine._q.empty():
        engine._q.get_nowait()()
    assert engine.run_id != previous_run
    assert not engine.history and not engine.decisions and not engine._auto_rearms
    assert not engine.scenarios


@pytest.mark.parametrize("stale,expected_run,action,status", [
    (True, "run", "forged", 409),
    (True, "run", "reset_plant", 200),
    (True, "run", "clear_faults", 200),
    (False, "old-run", "forged", 409),
    (False, "run", "forged", 200),
])
def test_http_controls_validate_freshness_and_run_identity(stale, expected_run, action, status):
    calls = []
    engine = SimpleNamespace(read=lambda *_: {"run_id": "run", "telemetry_stale": stale},
                             submit=lambda action, **kwargs: calls.append((action, kwargs)) or "queued")
    handler_class = make_handler(engine)
    handler = handler_class.__new__(handler_class)
    body = json.dumps({"action": action, "expected_run_id": expected_run}).encode()
    handler.path = "/api/action"
    handler.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json", "Host": "127.0.0.1:8765"}
    handler.server = SimpleNamespace(server_port=8765)
    handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
    response = {}
    handler.send_response = lambda code: response.update(status=code)
    handler.send_header = lambda *_: None
    handler.end_headers = lambda: None
    handler.do_POST()
    assert response["status"] == status
    assert len(calls) == (1 if status == 200 else 0)
    if calls:
        assert calls[0][1]["expected_run_id"] == expected_run
