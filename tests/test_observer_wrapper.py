"""Fast tests for monitor/observer.py: routing, flooring and degrade-to-zero (Ollama faked)."""
from __future__ import annotations

import json

from contracts import PlantState, SupervisoryTargets
from monitor import observer as O
from monitor import observer_v2_format as V2

STATE = PlantState(pressure=2700.0, level=44.18, f1_flow=384.0, f2_flow=21.2, purge_flow=717.9,
                   product_flow=127.5, a_in_purge=0.47, b_in_purge=0.14, c_in_purge=0.39,
                   f1_valve_pos=31.45, f2_valve_pos=13.24, purge_valve_pos=0.39,
                   product_valve_pos=47.03, cost=0.0)
INTENT = SupervisoryTargets(pressure_sp=2700.6, level_sp=44.18, production_rate_sp=100.0, a_comp_sp=47.0,
                            rationale="In band; holding design targets.")


def make(replies):
    seen = []

    def post(url, body, timeout):
        seen.append(json.loads(body["messages"][1]["content"])["input"])
        r = replies(seen[-1])
        if isinstance(r, Exception):
            raise r
        return {"message": {"content": r}} if isinstance(r, str) else r

    return O.Observer(background=False, http_post=post), seen


def run(obs, seconds=40.0):
    t = 0.0
    while t < seconds:
        obs.feed(t, STATE, INTENT)
        t += 0.1


def verdict(label, score=0.8):
    return json.dumps({"label": label, "score": score, "reason": "r"})


def test_proposal_attack_label_reaches_keel_and_telemetry_label_does_not():
    obs, seen = make(lambda kind: verdict("rationale_mismatch") if kind == "proposals" else verdict("sensor_fault"))
    run(obs)
    assert {"proposals", "telemetry"} <= set(seen)
    assert obs.last["telemetry"].label == "sensor_fault"          # shown ...
    assert obs.suspicion() == 0.8                                  # ... but only proposals count


def test_telemetry_only_alarm_never_feeds_keel():
    obs, _ = make(lambda kind: verdict("normal", 0) if kind == "proposals" else verdict("excursion", 0.9))
    run(obs)
    assert obs.last["telemetry"].label == "excursion" and obs.suspicion() == 0.0


def test_low_score_attack_is_floored_and_normal_is_zero():
    obs, _ = make(lambda kind: verdict("setpoint_drift", 0.05))
    run(obs)
    assert obs.suspicion() == O.SCORE_FLOOR
    obs2, _ = make(lambda kind: verdict("normal", 0.0))
    run(obs2)
    assert obs2.suspicion() == 0.0


def test_bad_output_or_backend_failure_degrades_to_zero():
    for reply in ("not json at all", verdict("provenance_violation_x"), RuntimeError("down"),
                  {"error": "model not found"}):
        obs, _ = make(lambda kind, r=reply: r)
        run(obs)
        assert obs.suspicion() == 0.0
        assert obs.failures >= 1 and obs.last["proposals"].label is None and obs.last["proposals"].note != "ok"


def test_proposals_wait_for_a_full_history_and_reset_clears_verdicts():
    obs, seen = make(lambda kind: verdict("rationale_mismatch"))
    run(obs, seconds=V2.HISTORY_N * O.PROPOSAL_EVERY_S - 6.0)      # not yet HISTORY_N proposals
    assert "proposals" not in seen
    run(obs)
    assert obs.suspicion() > 0
    obs.reset()
    assert obs.suspicion() == 0.0 and obs.snapshot()["proposals"] is None


def test_live_inputs_use_the_exact_v2_format():
    bodies = []
    obs = O.Observer(background=False, http_post=lambda u, b, t: bodies.append(b) or {"message": {"content": verdict("normal", 0)}})
    run(obs)
    for b in bodies:
        assert b["messages"][0]["content"] == V2.SYSTEM_PROMPT and b["format"] == "json"
        u = json.loads(b["messages"][1]["content"])
        if u["input"] == "telemetry":
            assert len(u["pressure"]) == V2.WINDOW_N and 2.0 <= u["window_s"] <= 3.0
        else:
            assert u["cycles"] == V2.HISTORY_N and u["proposals"][-1]["provenance"] == V2.OPERATOR_CHAIN


# ---- v3 path (served first; monitor/observer_v3_format) ---------------------------------
from contracts import ControlCommand
from monitor import observer_v3_format as V3
from test_observer_v3_format import rebuild

CMD = ControlCommand(f1_valve=31.45, f2_valve=13.24, purge_valve=0.3926, product_valve=52.0)


def make_v3(replies, **kw):
    bodies = []

    def post(url, body, timeout):
        bodies.append(body)
        r = replies(json.loads(body["messages"][1]["content"])["input"])
        if isinstance(r, Exception):
            raise r
        return {"message": {"content": r}} if isinstance(r, str) else r

    return O.Observer(fmt=V3, model="scram-observer-v3", num_ctx=3072, background=False, http_post=post, **kw), bodies


def run_v3(obs, seconds=40.0, cmd=CMD, start=0.0):
    t = start
    while t < start + seconds:
        obs.feed(t, STATE, INTENT, commanded=cmd)
        t += 0.1


def test_v3_live_built_telemetry_passes_the_v3_parity_gate():
    obs, bodies = make_v3(lambda kind: verdict("normal", 0))
    run_v3(obs)
    tel = [b for b in bodies if json.loads(b["messages"][1]["content"])["input"] == "telemetry"]
    assert tel and all(b["options"]["num_ctx"] == 3072 for b in bodies)
    for b in tel:
        assert b["messages"][0]["content"] == V3.SYSTEM_PROMPT
        user = b["messages"][1]["content"]
        assert rebuild(user) == user                         # byte-for-byte through the v3 module
        u = json.loads(user)
        assert len(u["pressure"]) == V3.WINDOW_N and 14.0 <= u["window_s"] <= 20.0   # training range
        assert u["f2_flow"][-1] == 21.2 and u["product_valve_cmd"][-1] == 52.0 and u["product_valve_pos"][-1] == 47.03
        assert all(isinstance(x, float) for k, xs in u.items() if isinstance(xs, list) and k != "scrammed" for x in xs)


def test_v3_live_window_has_the_stored_key_order():
    from test_observer_v3_format import telemetry_key_order
    obs, bodies = make_v3(lambda kind: verdict("normal", 0))
    run_v3(obs)
    u = next(json.loads(b["messages"][1]["content"]) for b in bodies
             if json.loads(b["messages"][1]["content"])["input"] == "telemetry")
    assert list(u) == telemetry_key_order()


def test_v3_takes_no_telemetry_sample_without_a_command():
    obs, bodies = make_v3(lambda kind: verdict("normal", 0))
    run_v3(obs, cmd=None)
    assert not any(json.loads(b["messages"][1]["content"])["input"] == "telemetry" for b in bodies)


def test_v3_telemetry_verdicts_never_reach_authority():
    for label in V3.ATTACK_LABELS:                           # even a proposal label on a telemetry window
        obs, _ = make_v3(lambda kind, lab=label: verdict("normal", 0) if kind == "proposals" else verdict(lab, 0.95))
        run_v3(obs)
        assert obs.last["telemetry"].label == label and obs.suspicion() == 0.0


def test_v3_proposal_side_feeds_authority_exactly_as_v2():
    obs, _ = make_v3(lambda kind: verdict("provenance_violation", 0.9) if kind == "proposals" else verdict("normal", 0))
    run_v3(obs)
    assert obs.suspicion() == 0.9
    obs2, _ = make_v3(lambda kind: verdict("setpoint_drift", 0.05) if kind == "proposals" else verdict("normal", 0))
    run_v3(obs2)
    assert obs2.suspicion() == O.SCORE_FLOOR


def test_chain_prefers_v3_then_v2_then_rule_monitor_only():
    pick = lambda installed, skip=(): O.make_observer(skip=skip, available=lambda m: m in installed)
    o = pick({"scram-observer-v3", "scram-observer"})
    assert o.model == "scram-observer-v3" and o.fmt is V3 and o.num_ctx == 3072
    o = pick({"scram-observer"})
    assert o.model == "scram-observer" and o.fmt is V2 and o.num_ctx is None
    assert pick({"scram-observer-v3", "scram-observer"}, skip=("scram-observer-v3",)).model == "scram-observer"
    assert pick(set()) is None
    assert pick({"scram-observer"}, skip=("scram-observer-v3", "scram-observer")) is None


def test_backend_errors_count_up_and_a_reply_resets_them():
    state = {"fail": True}
    obs, _ = make_v3(lambda kind: RuntimeError("model not loaded") if state["fail"] else verdict("normal", 0))
    run_v3(obs)
    assert obs.consecutive_errors >= O.MAX_CONSECUTIVE_ERRORS and obs.suspicion() == 0.0
    state["fail"] = False
    run_v3(obs, seconds=10.0, start=40.0)
    assert obs.consecutive_errors == 0
    bad, _ = make_v3(lambda kind: "not json")             # a bad OUTPUT is not a backend error
    run_v3(bad)
    assert bad.consecutive_errors == 0 and bad.failures >= 1
