"""Regression tests for the 2026-10-23 code review fixes (fast: no container, no model)."""
from __future__ import annotations

import json
import math
import threading

import pytest

from shared.contracts import ControlCommand, PlantState, SupervisoryTargets
from l0_plant.plant_service import PlantService
from l0_plant.provenance_gate import ProvenanceGate
from l3_brain.operator import AIOperator
from monitor import governance as G
from monitor import observer as O
from monitor.observer import Verdict

DESIGN_STATE = {"f1_valve_pos": 31.45, "f2_valve_pos": 13.24, "purge_valve_pos": 0.39,
                "product_valve_pos": 47.03, "e_stop": 0}
MEAS = {"pressure": 2700.0, "liquid_level": 44.2}


class FakeClient:
    def __init__(self, raw):
        self.raw, self.writes = raw, []

    def read(self):
        return self.raw

    def write_inputs(self, inputs):
        self.writes.append(inputs)
        return self.raw


def _svc(raw, gate=None):
    svc = PlantService(gate=gate)
    svc.client = FakeClient(raw)
    return svc


# ---- L0: plant service -------------------------------------------------------------------
def test_missing_safety_channel_trips_as_sensor_fault_not_level_low():
    svc = _svc({"measured": MEAS, "state": DESIGN_STATE})          # no "outputs" at all
    svc.read()
    assert svc.safety.tripped and svc.safety.cause == "sis_sensor_fault"


def test_rejected_command_with_no_last_good_holds_instead_of_crashing():
    raw = {"measured": MEAS, "state": DESIGN_STATE, "outputs": {"pressure": 2700.0, "liquid_level": 44.2}}
    gate = ProvenanceGate(b"k")                                    # never seeded: last_good is None
    svc = _svc(raw, gate)
    svc.apply(ControlCommand(f1_valve=100.0, f2_valve=100.0, purge_valve=0.0, product_valve=0.0))  # unsigned
    assert svc.client.writes[-1] == {"e_stop": 0}                  # no valve setpoints: valves held


def test_reset_with_missing_safety_channel_stays_latched():
    svc = _svc({"measured": MEAS, "state": DESIGN_STATE})
    svc.read()
    assert svc.reset_interlock() is False and svc.safety.tripped


# ---- L3: authority fails closed --------------------------------------------------------------
class _Fixed:
    def decide(self, state):
        return SupervisoryTargets(pressure_sp=2900.0, level_sp=44.0, production_rate_sp=120.0,
                                  a_comp_sp=47.0, rationale="r")


def _state():
    return PlantState(pressure=2700.0, level=44.2, f1_flow=0, f2_flow=0, purge_flow=0, product_flow=100.0,
                      a_in_purge=0.47, b_in_purge=0, c_in_purge=0, f1_valve_pos=31.45, f2_valve_pos=13.24,
                      purge_valve_pos=0.39, product_valve_pos=47.03, cost=0.0)


def test_nan_authority_holds_instead_of_granting_full_authority():
    t = AIOperator(policy=_Fixed(), authority_fn=lambda: math.nan).step(_state())
    assert t.authority == 0.0 and t.pressure_sp is None and t.production_rate_sp is None


# ---- L2: governance forgets observer evidence that has gone away -----------------------------
def _v(t, label="provenance_violation", score=0.9):
    return Verdict(t, "proposals", label, score, "r")


def test_observer_evidence_clears_when_the_observer_disappears():
    g = G.Governor()
    g.update(0.0, 0.0, "none", _v(0.0))
    s = g.update(5.0, 0.0, "none", _v(5.0))
    assert s.corroborated >= 0.9                                   # two consecutive: corroborated
    s = g.update(6.0, 0.0, "none", None)                           # proposal path fell back to none
    assert s.corroborated == 0.0 and s.ceiling == 1.0


def test_stale_observer_verdict_stops_holding_the_ceiling():
    g = G.Governor()
    g.update(0.0, 0.0, "none", _v(0.0))
    g.update(5.0, 0.0, "none", _v(5.0))
    s = g.update(5.0 + G.OBS_STALE_S + 1.0, 0.0, "none", _v(5.0))  # same verdict, never refreshed
    assert s.corroborated == 0.0


# ---- L2: observer never holds the shared gate after an error ---------------------------------
def test_observer_releases_the_gate_when_building_a_prompt_fails():
    class BadFmt:
        WINDOW_N, HISTORY_N, PROPOSAL_LABELS = 6, 1, ()
        TELEMETRY_FIELDS = ()
        @staticmethod
        def proposal_dict(intent, rationale): return {}
        @staticmethod
        def proposals_to_json(ps): raise KeyError("missing field")
    gate = threading.Semaphore(1)
    obs = O.Observer(fmt=BadFmt, kinds=("proposals",), background=False, gate=gate,
                     http_post=lambda *a: {"message": {"content": "{}"}})
    with pytest.raises(KeyError):
        obs.feed(10.0, _state(), SupervisoryTargets(pressure_sp=2700.0, rationale="x"))
    assert gate.acquire(blocking=False)                            # not leaked


# ---- live engine: action errors are not "plant connection lost" ------------------------------
def test_bad_action_is_logged_not_treated_as_a_lost_connection():
    from live.engine import Engine
    eng = Engine(reset=False)
    eng._run_action(lambda: (_ for _ in ()).throw(ValueError("actuator fault needs valve in ...")))
    assert eng.events[-1]["kind"] == "error" and "action failed" in eng.events[-1]["msg"]
    with pytest.raises(ConnectionError):                           # real plant I/O still reconnects
        eng._run_action(lambda: (_ for _ in ()).throw(ConnectionError("simulator closed")))


# ---- sim_app: a null valve reading does not crash ingest -------------------------------------
def test_sim_app_ingest_tolerates_null_valve_positions():
    from sim_app.server import Plant
    p = Plant()
    p._ingest({"outputs": {"pressure": 2700.0, "liquid_level": 44.2},
               "state": {"f1_valve_pos": None, "f2_valve_pos": 13.2, "purge_valve_pos": 0.4,
                         "product_valve_pos": 47.0}})
    assert p.setpoints["f1"] == 0.0 and p.history[-1][8] == 0.0


# ---- control-room server: malformed requests get a clean 400 ---------------------------------
def test_control_room_server_rejects_malformed_requests():
    import io
    from live.server import make_handler

    class _Eng:
        def read(self, h, e): return {}
        def submit(self, a, **kw): return "queued"

    H = make_handler(_Eng())

    def call(method, path, body=b""):
        h = H.__new__(H)
        h.path, h.rfile, h.wfile = path, io.BytesIO(body), io.BytesIO()
        h.headers = {"Content-Length": str(len(body))}
        h.request_version, h.requestline, h.command = "HTTP/1.1", f"{method} {path} HTTP/1.1", method
        sent = {}
        h.send_response = lambda code, msg=None: sent.setdefault("code", code)
        h.send_header = lambda *a: None
        h.end_headers = lambda: None
        getattr(h, f"do_{method}")()
        return sent["code"], h.wfile.getvalue()

    assert call("GET", "/api/state?h=abc")[0] == 400
    code, out = call("POST", "/api/action", json.dumps(["forged"]).encode())
    assert code == 400 and b"JSON object" in out
    assert call("POST", "/api/action", json.dumps({"action": "forged"}).encode())[0] == 200
