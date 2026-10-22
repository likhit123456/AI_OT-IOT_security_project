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
