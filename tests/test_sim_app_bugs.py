"""Regression tests for the sim_app bug fixes (no network, no plant: fakes only)."""
import json
import threading
import time

import pytest
from fastapi import HTTPException

import sim_app.ai as ai
import sim_app.server as server
from contracts import SupervisoryTargets


# ---- fakes ---------------------------------------------------------------------

class FakeSim:
    """GrficsClient stand-in with the simulator's e_stop semantics (TE_process.cc):
    e_stop forces valves to 0/0/100/100, ignores valve setpoints while on, and the
    forced positions persist after release."""

    def __init__(self, pressure=2700.6):
        self.v = {"f1": 31.45, "f2": 13.24, "purge": 0.0, "product": 47.03}
        self.e_stop, self.pressure, self.fail_reads = False, pressure, 0

    def _raw(self):
        return {"outputs": {"pressure": self.pressure, "liquid_level": 44.18},
                "measured": {"pressure": self.pressure, "liquid_level": 44.18},
                "state": {**{f"{k}_valve_pos": x for k, x in self.v.items()}, "e_stop": self.e_stop}}

    def read(self):
        if self.fail_reads:
            self.fail_reads -= 1
            raise ConnectionError("socket hiccup")
        return self._raw()

    def write_inputs(self, inputs):
        if "e_stop" in inputs:
            self.e_stop = bool(inputs["e_stop"])
        if self.e_stop:
            self.v = {"f1": 0.0, "f2": 0.0, "purge": 100.0, "product": 100.0}
        else:
            for k in self.v:
                if f"{k}_valve_sp" in inputs:
                    self.v[k] = float(inputs[f"{k}_valve_sp"])
        return self._raw()

    def close(self):
        pass


def make_plant(sim=None):
    p = server.Plant()
    p._client = sim or FakeSim()
    p._ingest(p.read())
    return p


# ---- bug 1: E-STOP leaves stale setpoints -----------------------------------------

def test_estop_release_steps_from_real_valve_positions():
    p = make_plant()
    p.manipulate("e_stop", None, 1)
    assert p.setpoints == {"f1": 0.0, "f2": 0.0, "purge": 100.0, "product": 100.0}
    p.manipulate("e_stop", None, 0)
    r = p.manipulate("purge", -1.0, None)          # used to send 1% (stale base 0%)
    assert r["setpoint"] == 99.0 and p._client.v["purge"] == 99.0


def test_valve_moves_refused_while_estop_on():
    p = make_plant()
    p.manipulate("e_stop", None, 1)
    with pytest.raises(HTTPException) as e:
        p.manipulate("f1", 5.0, None)
    assert e.value.status_code == 409 and "E-STOP" in e.value.detail


def test_setpoints_follow_external_writes():
    p = make_plant()
    p._client.write_inputs({"product_valve_sp": 12.0})     # someone else moved it
    p._ingest(p.read())
    assert p.manipulate("product", 1.0, None)["setpoint"] == 13.0


# ---- bug 9: concurrent resets ------------------------------------------------------

def test_second_concurrent_reset_is_refused(monkeypatch):
    gate = threading.Event()

    class Pt:
        pressure, level, cycles_to_settle = 2700.6, 44.18, 10

    def slow_reset():
        gate.wait(2)
        return Pt()

    monkeypatch.setattr(server, "reset_and_settle", slow_reset)
    p = make_plant()
    t = threading.Thread(target=p.reset)
    t.start()
    time.sleep(0.1)
    with pytest.raises(HTTPException) as e:
        p.reset()
    assert e.value.status_code == 409
    gate.set()
    t.join(3)
    assert not p.resetting


# ---- bug 3: the open/close check flipped a correct reading -------------------------

def fake_llm(reply):
    return lambda u, b, t: {"message": {"content": json.dumps(reply)}}


@pytest.mark.parametrize("text,model,want", [
    ("open the purge 10% to lower the pressure", 10, 10),     # goal word "lower" is not the valve
    ("close product by 3", 3, -3),                             # unambiguous close
    ("open product 10%", -10, 10),                             # unambiguous open, model sign wrong
    ("cut feed 2 to raise the level", -4, -4),                 # mixed words: trust the model
])
def test_direction_check_only_when_unambiguous(text, model, want):
    r = ai.parse_command(text, post=fake_llm({"action": "adjust", "valve": "purge", "amount": model}))
    assert r["amount"] == want


# ---- rescue loop: fakes for the model ---------------------------------------------

class FakeOp:
    """OllamaOperator stand-in: fixed targets, optional delay and failure outcome."""
    delay, outcome = 0.0, "ok"

    def __init__(self, **kw):
        self.model, self.log = "scram-operator", []

    def decide(self, state):
        time.sleep(self.delay)
        self.log.append({"outcome": self.outcome, "latency_s": self.delay})
        if self.outcome != "ok":
            return SupervisoryTargets(rationale="hold: model unavailable")
        return SupervisoryTargets(pressure_sp=2600.6, level_sp=44.18, production_rate_sp=70.0,
                                  a_comp_sp=47.0, rationale="backing off")


class RecPlant:
    """Rescue-facing plant fake that records which thread wrote valves."""

    def __init__(self, pressure=2980.0):
        self.sim, self.ruptured, self.resetting, self.writes = FakeSim(pressure), False, False, []
        self.raw = self.sim._raw()

    def read(self):
        self.raw = self.sim.read()
        return self.raw

    def write(self, inputs):
        self.raw = self.sim.write_inputs(inputs)
        return self.raw

    def apply_valves(self, valves, source="manual"):
        self.writes.append((threading.current_thread().name, time.monotonic()))
        self.raw = self.sim.write_inputs({f"{k}_valve_sp": v for k, v in valves.items()})


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(ai, "TICK_S", 0.05)
    monkeypatch.setattr(ai, "OllamaOperator", FakeOp)
    FakeOp.delay, FakeOp.outcome = 0.0, "ok"


def wait_until(cond, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def rescue_threads():
    return [t for t in threading.enumerate() if t.name.startswith("ai-rescue-") and t.is_alive()]


# ---- bug 2: Stop -> Save within a decision ran two loops ---------------------------

def test_quick_stop_then_save_leaves_one_loop(fast):
    FakeOp.delay = 0.3                                          # stop lands mid-decision
    plant, act = RecPlant(), ai.Activity()
    r = ai.Rescue(plant, act)
    r.start()
    time.sleep(0.1)
    r.stop()
    r.start()
    assert wait_until(lambda: len(rescue_threads()) == 1, 2.0)
    time.sleep(0.5)
    names = {n for n, _ in plant.writes[-3:]}
    assert names == {f"ai-rescue-{r._run_id}"} and r.active
    r.stop()
    assert wait_until(lambda: not rescue_threads())


# ---- bug 6: stop during a decision must not move valves -----------------------------

def test_stop_during_decision_writes_nothing_after(fast):
    FakeOp.delay = 0.3
    plant, act = RecPlant(), ai.Activity()
    r = ai.Rescue(plant, act)
    r.start()
    time.sleep(0.1)
    r.stop()
    t_stop = time.monotonic()
    assert wait_until(lambda: not rescue_threads())
    assert all(t < t_stop for _, t in plant.writes)


# ---- bug 4: one failed read used to end the rescue ---------------------------------

def test_transient_read_failure_is_retried(fast):
    plant, act = RecPlant(), ai.Activity()
    plant.sim.fail_reads = 2
    r = ai.Rescue(plant, act)
    r.start()
    assert wait_until(lambda: len(plant.writes) >= 2)
    assert r.active and sum("retrying" in x["text"] for x in act.items) == 2
    r.stop()


def test_persistent_read_failure_disengages_loudly(fast):
    plant, act = RecPlant(), ai.Activity()
    plant.sim.fail_reads = 99
    r = ai.Rescue(plant, act)
    r.start()
    assert wait_until(lambda: not r.active)
    assert act.items[-1]["kind"] == "error" and "unreachable" in act.items[-1]["text"]


# ---- bug 5: model down used to freeze valves for 10 minutes --------------------------

def test_model_failures_disengage_after_three(fast):
    FakeOp.outcome = "error: ConnectionRefusedError: ollama down"
    plant, act = RecPlant(), ai.Activity()
    r = ai.Rescue(plant, act)
    r.start()
    assert wait_until(lambda: not r.active)
    last = act.items[-1]
    assert last["kind"] == "error" and "no usable decision 3 times" in last["text"]
    assert plant.writes == []


# ---- bug 8: low-pressure recovery boost ---------------------------------------------

def test_low_pressure_boost_scales_with_deficit():
    t = SupervisoryTargets(pressure_sp=2700.6, level_sp=44.18, production_rate_sp=100.0, a_comp_sp=47.0)
    st = lambda p: ai.plant_state({"measured": {"pressure": p, "liquid_level": 44.18}, "state": {}, "outputs": {}})
    far = ai.regulate(t, st(2300.6))                                 # 400 kPa short: feeds near full
    near = ai.regulate(t, st(2660.6))                                # 40 kPa short: tapering
    assert far["f1"] == 100.0 and far["f2"] == pytest.approx(ai.DESIGN_VALVES["f2"] * 3.2)
    assert near["f1"] == pytest.approx(ai.DESIGN_VALVES["f1"] * 1.5)
    assert ai.regulate(t, st(2700.6))["f1"] == pytest.approx(ai.DESIGN_VALVES["f1"])


# ---- GRFICS steady_state() purge bug (b[3] out-of-bounds read) ------------------------

def test_design_purge_matches_te_steady_state_equations():
    """Re-derive the purge the simulator meant to set (TE_process.cc steady_state)."""
    import math
    import numpy as np
    from scripts.reset_plant import DESIGN_PURGE_VALVE_PCT
    P, xA, prod, kpar, nc, ya1, yb1 = 2700.0, 0.47, 100.0, 0.00117, 0.4, 0.485, 0.005
    Pa = P * xA
    Pc = (prod / (kpar * Pa ** 1.2)) ** (1 / nc)
    xC, xB = Pc / P, (P - Pa - Pc) / P
    f1, f2, purge = np.linalg.solve(np.array([[ya1, 1, -xA], [yb1, 0, -xB], [1 - ya1 - yb1, 0, -xC]]),
                                    np.array([prod, 0, prod]))
    assert purge / (0.352 * math.sqrt(P - 100)) == pytest.approx(DESIGN_PURGE_VALVE_PCT, abs=1e-4)
    assert f1 / 6.4046 == pytest.approx(ai.DESIGN_VALVES["f1"], abs=0.01)


def test_regulator_keeps_design_purge_open_at_setpoint():
    t = SupervisoryTargets(pressure_sp=2700.6, level_sp=44.18, production_rate_sp=100.0, a_comp_sp=47.0)
    st = ai.plant_state({"measured": {"pressure": 2700.6, "liquid_level": 44.18}, "state": {}, "outputs": {}})
    assert ai.regulate(t, st)["purge"] == pytest.approx(ai.DESIGN_VALVES["purge"]) and ai.DESIGN_VALVES["purge"] > 0.3


def test_reset_applies_design_purge(monkeypatch):
    import scripts.reset_plant as rp
    sent = []

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def makefile(self, mode):
            class F:
                def write(self, b): sent.append(json.loads(b))
                def flush(self): pass
                def readline(self):
                    return json.dumps({"state": {"purge_valve_pos": sent[-1]["data"]["inputs"]["purge_valve_sp"]}}).encode()
            return F()

    monkeypatch.setattr(rp, "restart_container", lambda: None)
    monkeypatch.setattr(rp, "wait_reachable", lambda: None)
    monkeypatch.setattr(rp, "wait_settled", lambda **kw: "settled")
    monkeypatch.setattr(rp.socket, "create_connection", lambda *a, **k: Conn())
    assert rp.reset_and_settle() == "settled"
    assert sent == [{"request": "write", "data": {"inputs": {"purge_valve_sp": rp.DESIGN_PURGE_VALVE_PCT}}}]


def test_design_purge_retries_when_the_write_is_dropped(monkeypatch):
    """Right after a restart the port proxy accepts and then drops the connection."""
    import scripts.reset_plant as rp
    replies = [b"", b"", None]                       # two dropped writes, then the simulator answers
    sent = []

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def makefile(self, mode):
            class F:
                def write(self, b): sent.append(b)
                def flush(self): pass
                def readline(self):
                    r = replies.pop(0)
                    return r if r is not None else json.dumps(
                        {"state": {"purge_valve_pos": rp.DESIGN_PURGE_VALVE_PCT}}).encode()
            return F()

    monkeypatch.setattr(rp.socket, "create_connection", lambda *a, **k: Conn())
    monkeypatch.setattr(rp.time, "sleep", lambda s: None)
    rp.set_design_purge()
    assert len(sent) == 3


# ---- several actions in one command ---------------------------------------------------

def fake_multi(actions):
    return lambda u, b, t: {"message": {"content": json.dumps({"actions": actions})}}


def test_parse_commands_splits_and_checks_each_clause():
    text = "open feed 1 to 100%, set purge to 20 and lower feed 2 by 2%"
    r = ai.parse_commands(text, post=fake_multi([
        {"clause": "open feed 1 to 100%", "action": "adjust", "valve": "f1", "amount": 100},   # to/by slip
        {"clause": "set purge to 20", "action": "set", "valve": "purge", "amount": 20},
        {"clause": "lower feed 2 by 2%", "action": "adjust", "valve": "f2", "amount": 2}]))     # sign slip
    got = [(a["action"], a["valve"], a["amount"]) for a in r["actions"]]
    assert got == [("set", "f1", 100.0), ("set", "purge", 20.0), ("adjust", "f2", -2.0)]
    assert [bool(a["corrections"]) for a in r["actions"]] == [True, False, True]


def test_parse_commands_checks_against_the_real_segment_when_clause_is_paraphrased():
    text = "set purge to 20, product to 50 and feed 2 to 10"
    r = ai.parse_commands(text, post=fake_multi([
        {"clause": "set product to 50", "action": "adjust", "valve": "product", "amount": 50}]  # not in text
        + [{"clause": "feed 2 to 10", "action": "set", "valve": "f2", "amount": 10}]))
    assert (r["actions"][0]["action"], r["actions"][0]["amount"]) == ("set", 50.0)          # "product to 50"


def test_parse_commands_bad_output_is_one_unknown():
    r = ai.parse_commands("x", post=lambda u, b, t: {"message": {"content": "nope"}})
    assert [a["action"] for a in r["actions"]] == ["unknown"]


def test_parse_commands_caps_the_number_of_actions():
    many = [{"clause": "c", "action": "adjust", "valve": "f1", "amount": 1}] * 20
    assert len(ai.parse_commands("c", post=fake_multi(many))["actions"]) == ai.MAX_ACTIONS


def _client(monkeypatch, actions):
    from fastapi.testclient import TestClient
    plant = make_plant()
    monkeypatch.setattr(server, "plant", plant)
    monkeypatch.setattr(server, "parse_commands", lambda text: {
        "model_output": "{}", "latency_s": 0.1,
        "actions": [{"clause": c, "action": a, "valve": v, "amount": n, "corrections": []} for c, a, v, n in actions]})
    return TestClient(server.app), plant


def test_command_endpoint_applies_all_actions_in_order(monkeypatch):
    client, plant = _client(monkeypatch, [("feed 1 to 40", "set", "f1", 40.0), ("purge +5", "adjust", "purge", 5.0),
                                          ("product to 30", "set", "product", 30.0)])
    r = client.post("/api/command", json={"text": "three things"})
    assert r.status_code == 200
    body = r.json()
    assert [st["ok"] for st in body["actions"]] == [True, True, True]
    assert (plant._client.v["f1"], plant._client.v["purge"], plant._client.v["product"]) == (40.0, 5.0, 30.0)
    assert body["parsed"]["valve"] == "f1" and body["result"]["setpoint"] == 40.0       # legacy fields


def test_command_endpoint_reports_partial_failure_and_keeps_going(monkeypatch):
    client, plant = _client(monkeypatch, [("feed 1 to 40", "set", "f1", 40.0), ("sing", "unknown", None, None),
                                          ("purge to 10", "set", "purge", 10.0)])
    body = client.post("/api/command", json={"text": "mixed"}).json()
    assert [st["ok"] for st in body["actions"]] == [True, False, True]
    assert plant._client.v["purge"] == 10.0


def test_command_endpoint_all_unknown_is_422(monkeypatch):
    client, _ = _client(monkeypatch, [("sing", "unknown", None, None)])
    assert client.post("/api/command", json={"text": "sing"}).status_code == 422


def test_command_endpoint_refused_actions_are_409(monkeypatch):
    client, plant = _client(monkeypatch, [("feed 1 to 40", "set", "f1", 40.0)])
    plant.manipulate("e_stop", None, 1)
    r = client.post("/api/command", json={"text": "feed 1 to 40"})
    assert r.status_code == 409 and "E-STOP" in r.json()["detail"]


def test_shut_without_number_is_set_zero_and_fully_open_is_100():
    for text, model, want in [("shut the product valve", 0, 0.0), ("close feed 2", -100, 0.0),
                              ("fully open the purge", 10, 100.0)]:
        r = ai.parse_command(text, post=fake_llm({"action": "adjust", "valve": "purge", "amount": model}))
        assert (r["action"], r["amount"]) == ("set", want), text


def test_clause_naming_two_valves_gets_both():
    text = "open feed 1 and feed 2 to 100% and shut the product valve"
    r = ai.parse_commands(text, post=fake_multi([
        {"clause": "open feed 1 and feed 2 to 100%", "action": "set", "valve": "f1", "amount": 100},
        {"clause": "shut the product valve", "action": "adjust", "valve": "product", "amount": 0}]))
    got = [(a["action"], a["valve"], a["amount"]) for a in r["actions"]]
    assert got == [("set", "f1", 100.0), ("set", "f2", 100.0), ("set", "product", 0.0)]
    assert r["actions"][1]["corrections"] and "skipped f2" in r["actions"][1]["corrections"][0]
