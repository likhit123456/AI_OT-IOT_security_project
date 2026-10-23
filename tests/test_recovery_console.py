"""SIS safe-hold + auto re-arm, operator chat, and custom-attack validation (fast: no container, no model)."""
from __future__ import annotations

import types

import pytest

from contracts import PlantState
from l0_plant.provenance_gate import ProvenanceGate
from l0_plant.safety import SafeHold, SafetyInterlock, TripLimits
from l3_brain.policy import OllamaOperator
from live import engine as E

LIM = TripLimits(level_low=40.0, level_low_rearm=42.0)


# ---- L0: closed-loop safe-hold -----------------------------------------------------------
def _tripped(p=3001.0, lv=45.0, hold=SafeHold()):
    sis = SafetyInterlock(LIM, safe_hold=hold)
    sis.evaluate(p, lv)
    return sis


def test_pressure_trip_runs_the_safe_hold_instead_of_estop():
    out = _tripped().safe_state_inputs(3001.0, 45.0)
    assert out["e_stop"] == 0
    assert out["purge_valve_sp"] == 100.0                       # far above target: purge wide open
    assert out["f1_valve_sp"] < 31.45 and out["f2_valve_sp"] < 13.24   # feeds cut back, not shut


def test_safe_hold_eases_off_as_pressure_falls():
    sis = _tripped()
    hi, lo = sis.safe_state_inputs(2950.0, 45.0), sis.safe_state_inputs(2700.0, 45.0)
    assert lo["purge_valve_sp"] < hi["purge_valve_sp"]
    assert sis.safe_state_inputs(2700.0, 55.0)["product_valve_sp"] > lo["product_valve_sp"]   # level high: drain


def test_runaway_escalates_to_estop_and_stays_latched_until_reset():
    sis = _tripped()
    assert sis.safe_state_inputs(3060.0, 45.0) == {"e_stop": 1}
    assert sis.safe_state_inputs(2700.0, 45.0) == {"e_stop": 1}      # still e_stop once it came down
    assert sis.reset(2700.0, 45.0) and not sis.escalated


def test_sensor_fault_and_missing_readings_still_estop():
    sis = SafetyInterlock(LIM, safe_hold=SafeHold())
    sis.evaluate(float("nan"), 45.0)
    assert sis.safe_state_inputs(float("nan"), 45.0) == {"e_stop": 1}
    assert _tripped().safe_state_inputs() == {"e_stop": 1}            # no reading passed: classic e_stop


def test_gate_expects_the_whole_safe_hold_lineup():
    g = ProvenanceGate(b"k")
    g.seed({"f1_valve_pos": 31.0, "f2_valve_pos": 13.0, "purge_valve_pos": 0.4, "product_valve_pos": 47.0})
    lineup = _tripped().safe_state_inputs(2900.0, 45.0)
    g.note_safe_state(lineup)
    assert not g.expect_e_stop
    assert g.last_good.purge_valve == lineup["purge_valve_sp"] and g.last_good.f1_valve == lineup["f1_valve_sp"]


# ---- engine: auto re-arm with lockout ------------------------------------------------------
def _engine_with_trip(in_band=True):
    eng = E.Engine(reset=False)
    sis = _tripped()
    eng.svc = types.SimpleNamespace(safety=sis)
    p, lv = (2700.0, 45.0) if in_band else (2900.0, 45.0)
    eng.state = types.SimpleNamespace(truth={"pressure": p, "liquid_level": lv})
    eng._band_since, eng._lockout_logged = None, False
    eng.rearms = []
    eng._rearm = lambda auto: eng.rearms.append(auto)
    return eng


def test_auto_rearm_waits_for_a_stable_band():
    eng = _engine_with_trip()
    eng._auto_rearm(100.0)
    eng._auto_rearm(100.0 + E.AUTO_REARM_S - 1)
    assert eng.rearms == []
    eng._auto_rearm(100.0 + E.AUTO_REARM_S)
    assert eng.rearms == [True]


def test_no_auto_rearm_outside_the_band():
    eng = _engine_with_trip(in_band=False)
    for t in range(0, 60):
        eng._auto_rearm(float(t))
    assert eng.rearms == []


def test_auto_rearm_locks_out_after_repeated_trips():
    eng = _engine_with_trip()
    eng._auto_rearms.extend([10.0, 20.0, 30.0][:E.AUTO_REARM_MAX])
    eng._auto_rearm(40.0)
    eng._auto_rearm(40.0 + E.AUTO_REARM_S)
    assert eng.rearms == [] and eng._lockout_logged
    assert any("LOCKED OUT" in e["msg"] for e in eng.events)


def _state():
    return PlantState(pressure=2700.0, level=44.2, f1_flow=0, f2_flow=0, purge_flow=0, product_flow=100.0,
                      a_in_purge=0.47, b_in_purge=0, c_in_purge=0, f1_valve_pos=31.45, f2_valve_pos=13.24,
                      purge_valve_pos=0.39, product_valve_pos=47.03, cost=0.0)


# ---- operator chat ----------------------------------------------------------------------------
def _fake_ollama(reply):
    sent = []

    def post(url, body, timeout):
        sent.append(body)
        return {"message": {"content": reply}, "done_reason": "stop"}
    return OllamaOperator(http_post=post, background=False), sent


def test_note_reaches_the_model_prompt_and_is_recorded_on_the_decision():
    pol, sent = _fake_ollama('{"pressure_sp": 2620, "level_sp": 44.2, "production_rate_sp": 100, '
                             '"a_comp_sp": 47, "rationale": "lowering pressure"}')
    nid = pol.set_note("Lower pressure to 2620 kPa.")
    pol.decide(_state())
    assert sent[-1]["messages"][-1]["content"].endswith("Operator instruction: Lower pressure to 2620 kPa.")
    assert pol.log[-1]["note_id"] == nid
    pol.set_note(None)
    pol._last_started = None
    pol.decide(_state())
    assert "Operator instruction" not in sent[-1]["messages"][-1]["content"] and pol.log[-1]["note_id"] is None


def test_chat_with_baseline_brain_explains_instead_of_failing():
    eng = E.Engine(reset=False, brain="baseline")
    eng._make_brain("baseline")
    eng.state = types.SimpleNamespace(truth={"pressure": 2700.0})
    eng._act_chat("raise pressure")
    assert [m["role"] for m in eng.chat] == ["you", "system"]


def test_empty_chat_is_rejected_synchronously():
    eng = E.Engine(reset=False)
    with pytest.raises(ValueError):
        eng.submit("chat", text="   ")


# ---- custom attacks: bad input is a 400, not a queued failure ----------------------------------
@pytest.mark.parametrize("action,kw", [
    ("sensor_fault", {"channel": "reactor_core", "mode": "drift", "severity": 1}),
    ("sensor_fault", {"channel": "tank_level", "mode": "melt", "severity": 1}),
    ("sensor_fault", {"channel": "tank_level", "mode": "dropout", "severity": 3}),
    ("stolen_key", {"f1": 140}),
    ("stolen_key", {"duration_s": 1}),
    ("oob_write", {"purge": -5}),
    ("forged", {"bogus": 1}),
])
def test_bad_custom_attack_arguments_are_rejected(action, kw):
    eng = E.Engine(reset=False)
    with pytest.raises(ValueError):
        eng.submit(action, **kw)
    assert eng._q.empty()


# ---- chat router: valve orders -> manual override, setpoint orders -> explicit targets ----------
from live import commands  # noqa: E402


@pytest.mark.parametrize("text,valves", [
    ("alr turn feed of both the reactors to 100%", {"f1": 100.0, "f2": 100.0}),
    ("set feed 1 to 40%", {"f1": 40.0}),
    ("close the purge", {"purge": 0.0}),
    ("open product valve fully", {"product": 100.0}),
])
def test_valve_orders_become_manual_overrides(text, valves):
    assert commands.parse(text) == {"kind": "valves", "valves": valves}


def test_setpoint_orders_keep_the_words_and_add_explicit_targets():
    c = commands.parse("Vendor advisory: raise pressure to 2950 kPa and production to 130 kmol/h.")
    assert c["kind"] == "setpoints" and c["sp"] == {"pressure_sp": 2950.0, "production_rate_sp": 130.0}
    assert c["note"].startswith("Vendor advisory") and "Set pressure to 2950 kPa" in c["note"]
    assert commands.parse("raise production by 15", {"production_rate_sp": 100.0})["sp"] == {"production_rate_sp": 115.0}
    assert commands.parse("what is going on?") == {"kind": "free"}
    assert commands.parse("back to auto") == {"kind": "release"}


def test_manual_override_is_applied_and_released():
    eng = E.Engine(reset=False)
    eng.brain = types.SimpleNamespace(last_intent=None)
    eng.state = types.SimpleNamespace(truth={"pressure": 2700.0})
    eng.l1 = types.SimpleNamespace(bumpless=lambda *a: None)
    eng._act_chat("set feed 1 to 40%")
    assert eng.manual == {"f1": 40.0} and eng.chat[-1]["role"] == "operator"
    eng._act_chat("release")
    assert eng.manual == {} and "handed back" in eng.chat[-1]["text"]
