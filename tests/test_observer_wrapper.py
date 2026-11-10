"""Fast tests for l2_monitor/observer.py: routing, flooring and degrade-to-zero (Ollama faked)."""
from __future__ import annotations

import json

from shared.contracts import PlantState, SupervisoryTargets
from l2_monitor import observer as O
from l2_monitor import observer_v2_format as V2

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


# ---- v3 path (served first; l2_monitor/observer_v3_format) ---------------------------------
from shared.contracts import ControlCommand
from l2_monitor import observer_v3_format as V3
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


# ---- split serving: proposals on v2 (authority), telemetry on v3 (display only) ---------
BOTH = {"scram-observer-v3", "scram-observer"}


def make_split(installed=BOTH, replies=lambda model, kind: verdict("normal", 0)):
    bodies = []

    def post(url, body, timeout):
        bodies.append(body)
        r = replies(body["model"], json.loads(body["messages"][1]["content"])["input"])
        if isinstance(r, Exception):
            raise r
        return {"message": {"content": r}} if isinstance(r, str) else r

    return O.make_split_observer(available=lambda m: m in installed, background=False, http_post=post), bodies


def run_split(obs, seconds=60.0, start=0.0):
    t = start
    while t < start + seconds:
        obs.feed(t, STATE, INTENT, commanded=CMD)
        t += 0.1


def test_split_routes_proposals_to_v2_and_telemetry_to_v3():
    obs, bodies = make_split()
    run_split(obs)
    by = {}
    for b in bodies:
        by.setdefault(json.loads(b["messages"][1]["content"])["input"], set()).add(
            (b["model"], b["messages"][0]["content"] == (V3.SYSTEM_PROMPT if b["model"].endswith("v3") else V2.SYSTEM_PROMPT),
             b["options"].get("num_ctx"), b["keep_alive"]))
    assert by == {"proposals": {("scram-observer", True, None, O.KEEP_ALIVE)},
                  "telemetry": {("scram-observer-v3", True, 3072, O.KEEP_ALIVE)}}


def test_split_live_telemetry_still_passes_the_v3_parity_gate():
    obs, bodies = make_split()
    run_split(obs)
    tel = [b["messages"][1]["content"] for b in bodies if b["model"] == "scram-observer-v3"]
    assert tel and all(rebuild(u) == u for u in tel)


def test_split_authority_comes_from_the_v2_proposal_path_only():
    obs, _ = make_split(replies=lambda m, k: verdict("actuator_fault", 0.9) if k == "telemetry" else verdict("normal", 0))
    run_split(obs)
    assert obs.last["telemetry"].label == "actuator_fault" and obs.suspicion() == 0.0
    obs, _ = make_split(replies=lambda m, k: verdict("provenance_violation", 0.9) if k == "proposals" else verdict("normal", 0))
    run_split(obs)
    assert obs.suspicion() == 0.9 and obs.snapshot()["models"] == {"proposals": "scram-observer",
                                                                  "telemetry": "scram-observer-v3"}


def test_split_paths_share_one_in_flight_call_and_keep_the_validated_rate():
    obs, bodies = make_split()
    run_split(obs, seconds=100.0)
    # each path at most once per SPLIT_CHECK_EVERY_S: ~0.4 calls/s combined, as in the co-residency check
    for model in ("scram-observer", "scram-observer-v3"):
        n = sum(b["model"] == model for b in bodies)
        assert 0 < n <= 100.0 / O.SPLIT_CHECK_EVERY_S + 1
    assert obs.paths["proposals"]._gate is obs.paths["telemetry"]._gate


def test_split_fallback_chains_per_path():
    # telemetry: v3 -> v2 -> nothing; proposals: v2 -> rule monitor only (never v3)
    o, _ = make_split(installed={"scram-observer"})
    assert o.model("proposals") == "scram-observer" and o.model("telemetry") == "scram-observer"
    o, _ = make_split(installed={"scram-observer-v3"})
    assert o.model("proposals") is None and o.model("telemetry") == "scram-observer-v3" and o.suspicion() == 0.0
    assert make_split(installed=set())[0] is None


def test_split_runtime_fallback_when_a_model_keeps_failing():
    down = {"scram-observer-v3"}
    obs, bodies = make_split(replies=lambda m, k: RuntimeError("model unloaded") if m in down else verdict("normal", 0))
    run_split(obs)
    assert obs.fallback() == [("telemetry", "scram-observer-v3", "scram-observer")]
    run_split(obs, seconds=30.0, start=60.0)
    assert obs.last["telemetry"].label == "normal" and obs.model("proposals") == "scram-observer"
    down.add("scram-observer")                              # now v2 fails too: both paths give up
    run_split(obs, seconds=60.0, start=90.0)
    assert sorted(obs.fallback()) == [("proposals", "scram-observer", None), ("telemetry", "scram-observer", None)]
    assert not obs.available and obs.suspicion() == 0.0


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
