"""
Fast tests for l3_brain/policy.py's LLMOperator: output parser/validator and the
operator's robustness behaviour. No network - the model client is a fake.
"""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts import PlantState
import l3_brain.policy as policy_mod
from l3_brain.policy import (SYSTEM_PROMPT, BaselineOperator, Envelope, LLMOperator,
                             OllamaOperator, build_state_prompt, make_policy, parse_llm_targets)
from l3_brain.prompt_format import DESIGN_POINT, measured_dict, state_to_json, system_prompt

GOOD = '{"pressure_sp": 2700, "level_sp": 50, "production_rate_sp": 100, "a_comp_sp": 47, "rationale": "nominal"}'


def make_state(**kw) -> PlantState:
    base = dict(pressure=2700.0, level=44.0, f1_flow=10.0, f2_flow=5.0, purge_flow=1.0,
                product_flow=100.0, a_in_purge=0.47, b_in_purge=0.2, c_in_purge=0.33,
                f1_valve_pos=31.0, f2_valve_pos=13.0, purge_valve_pos=0.0,
                product_valve_pos=47.0, cost=0.0)
    base.update(kw)
    return PlantState(**base)


# ---- parse_llm_targets -------------------------------------------------------

def test_parses_valid_output():
    t, note = parse_llm_targets(GOOD)
    assert note == "ok"
    assert (t.pressure_sp, t.level_sp, t.production_rate_sp, t.a_comp_sp) == (2700, 50, 100, 47)
    assert t.rationale == "nominal"


def test_clamps_to_envelope_and_says_so():
    t, note = parse_llm_targets('{"pressure_sp": 3500, "level_sp": 2, "production_rate_sp": 999, '
                                '"a_comp_sp": 47, "rationale": "push"}')
    assert (t.pressure_sp, t.level_sp, t.production_rate_sp) == (2900.0, 30.0, 180.0)
    assert "clamped" in note and "pressure_sp" in t.rationale


def test_null_and_missing_setpoints_mean_hold_that_field():
    t, _ = parse_llm_targets('{"pressure_sp": 2650, "level_sp": null, "rationale": "x"}')
    assert t.pressure_sp == 2650
    assert t.level_sp is None and t.production_rate_sp is None and t.a_comp_sp is None


def test_tolerates_code_fence_and_surrounding_prose():
    t, _ = parse_llm_targets("Sure!\n```json\n" + GOOD + "\n```")
    assert t is not None and t.pressure_sp == 2700


def test_custom_envelope_is_respected():
    t, _ = parse_llm_targets(GOOD, Envelope(pressure=(2000.0, 2500.0)))
    assert t.pressure_sp == 2500.0


@pytest.mark.parametrize("bad", [
    "", "   ", "no json here", "{not json}", "[1, 2, 3]", '"just a string"',
    '{"pressure_sp": "2700"}', '{"pressure_sp": true}', '{"pressure_sp": NaN}',
    '{"pressure_sp": Infinity}', '{"level_sp": [50]}', None, 42,
])
def test_rejects_malformed_output(bad):
    t, note = parse_llm_targets(bad)
    assert t is None and note


def test_ignores_extra_keys_including_valve_positions():
    t, _ = parse_llm_targets('{"pressure_sp": 2700, "f1_valve": 100, "product_valve": 0, '
                             '"e_stop": 1, "rationale": "r"}')
    assert t is not None
    assert not hasattr(t, "f1_valve") and not hasattr(t, "e_stop")


def test_rationale_is_truncated():
    t, _ = parse_llm_targets('{"pressure_sp": 2700, "rationale": "' + "x" * 5000 + '"}')
    assert len(t.rationale) <= 200


# ---- prompt: measured only ---------------------------------------------------

def test_prompt_uses_measured_fields_and_never_truth():
    st = make_state(truth={"pressure": 123456.0, "liquid_level": 987654.0})
    prompt = build_state_prompt(st)
    assert json.loads(prompt)["pressure"] == 2700.0 and json.loads(prompt)["level"] == 44.0
    assert "123456" not in prompt and "987654" not in prompt


# ---- train/serve match: served prompt == fine-tune dataset format ---------------

def test_served_user_turn_matches_dataset_format():
    # collect_states records measured_dict(state); build_dataset serializes that record
    st = make_state(pressure=2873.23761, a_in_purge=0.473312)
    recorded = json.loads(json.dumps(measured_dict(st)))        # round-trip through states.jsonl
    assert build_state_prompt(st) == state_to_json(recorded)
    assert json.loads(build_state_prompt(st))["a_in_purge"] == 0.4733   # mole fraction, not mol %


def test_served_system_prompt_uses_real_design_point():
    assert SYSTEM_PROMPT == system_prompt()
    assert "level 44.18 %" in SYSTEM_PROMPT and "trips at 3000 kPa" in SYSTEM_PROMPT


def test_design_point_constant_matches_sim_reading():
    path = Path(__file__).resolve().parent.parent / "training" / "design_point.json"
    if not path.exists():
        pytest.skip("training/design_point.json not collected yet")
    d = json.loads(path.read_text())
    assert {k: d[k] for k in DESIGN_POINT} == DESIGN_POINT


# ---- operator behaviour (fake client) -----------------------------------------

class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        r = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def reply(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])


def make_op(replies, clock=None, **kw):
    return LLMOperator(client=FakeClient(replies), background=False,
                       clock=clock or time.monotonic, **kw)


def is_hold(t):
    return (t.pressure_sp is None and t.level_sp is None
            and t.production_rate_sp is None and t.a_comp_sp is None)


def test_returns_model_decision():
    op = make_op([reply(GOOD)])
    t = op.decide(make_state())
    assert t.pressure_sp == 2700 and not is_hold(t)


def test_caches_between_intervals_and_refreshes_after():
    now = [0.0]
    op = make_op([reply(GOOD), reply(GOOD.replace("2700", "2600"))], clock=lambda: now[0],
                 interval_s=20.0)
    assert op.decide(make_state()).pressure_sp == 2700
    now[0] = 10.0
    assert op.decide(make_state()).pressure_sp == 2700     # cached, no new call
    assert op._client.calls == 1
    now[0] = 21.0
    assert op.decide(make_state()).pressure_sp == 2600     # interval elapsed
    assert op._client.calls == 2


@pytest.mark.parametrize("r", [
    TimeoutError("timed out"), ConnectionError("down"), RuntimeError("boom"),
    reply("garbage, not json"), reply(GOOD, stop="refusal"), reply(GOOD, stop="max_tokens"),
    reply('{"pressure_sp": "high"}'),
])
def test_any_failure_degrades_to_hold_and_never_raises(r):
    t = make_op([r]).decide(make_state())
    assert is_hold(t) and t.rationale.startswith("hold:")


def test_missing_sdk_or_key_degrades_to_hold(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    op = LLMOperator(background=False)          # real client path, no credentials
    t = op.decide(make_state())
    assert is_hold(t)
    assert op.log and op.log[-1]["outcome"].startswith("error")


def test_failed_call_is_not_retried_every_cycle():
    op = make_op([RuntimeError("boom")], interval_s=20.0)
    for _ in range(5):
        op.decide(make_state())
    assert op._client.calls == 1


def test_scrammed_plant_holds_without_calling_model():
    op = make_op([reply(GOOD)])
    t = op.decide(make_state(scrammed=True, scram_reason="level"))
    assert is_hold(t) and t.authority == 0.0 and op._client.calls == 0


def test_background_mode_never_blocks_decide():
    gate = threading.Event()

    class SlowClient(FakeClient):
        def create(self, **kw):
            gate.wait(5)
            return super().create(**kw)

    op = LLMOperator(client=SlowClient([reply(GOOD)]), background=True)
    t0 = time.monotonic()
    first = op.decide(make_state())
    assert time.monotonic() - t0 < 0.5           # returned immediately
    assert is_hold(first)                         # nothing decided yet
    gate.set()
    for _ in range(100):
        if op.calls:
            break
        time.sleep(0.02)
    assert op.decide(make_state()).pressure_sp == 2700


# ---- selection ----------------------------------------------------------------

def test_make_policy_defaults_to_baseline(monkeypatch):
    monkeypatch.delenv("SCRAM_POLICY", raising=False)
    monkeypatch.delenv("SCRAM_OPERATOR", raising=False)
    assert isinstance(make_policy(), BaselineOperator)


def test_make_policy_llm_without_credentials_falls_back_to_baseline(monkeypatch):
    monkeypatch.setenv("SCRAM_POLICY", "llm")
    monkeypatch.delenv("SCRAM_OPERATOR", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert isinstance(make_policy(), BaselineOperator)


def test_make_policy_llm_with_key_selects_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert isinstance(make_policy("llm"), LLMOperator)


# ---- Ollama path (locally fine-tuned model; HTTP mocked, no network) ------------

# a real reply from the deployed scram-operator (val pressure_backoff example)
TRAINED_REPLY = ('{"pressure_sp": 2665.6, "level_sp": 44.18, "production_rate_sp": 89.5, '
                 '"a_comp_sp": 47.0, "rationale": "Pressure 2873.2 kPa is 126.8 kPa from trip; '
                 'backing off."}')


class FakeOllama:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, url, body, timeout_s):
        self.requests.append((url, body, timeout_s))
        r = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def ollama_reply(text, done_reason="stop"):
    return {"model": "scram-operator", "message": {"role": "assistant", "content": text},
            "done": True, "done_reason": done_reason}


def test_ollama_path_parses_trained_model_reply():
    fake = FakeOllama([ollama_reply(TRAINED_REPLY)])
    op = OllamaOperator(http_post=fake, background=False)
    st = make_state(pressure=2873.2)
    t = op.decide(st)
    assert (t.pressure_sp, t.level_sp, t.production_rate_sp, t.a_comp_sp) == (2665.6, 44.18, 89.5, 47.0)
    assert t.rationale.startswith("Pressure 2873.2 kPa") and op.log[-1]["outcome"] == "ok"
    url, body, _ = fake.requests[0]
    assert url == "http://127.0.0.1:11434/api/chat" and body["model"] == "scram-operator"
    assert body["options"]["temperature"] == 0 and body["stream"] is False
    # served exactly the fine-tune format: dataset system prompt + measured-state JSON
    assert body["messages"] == [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": state_to_json(st)}]


def test_ollama_reply_is_clamped_to_envelope():
    fake = FakeOllama([ollama_reply('{"pressure_sp": 2990, "level_sp": 30.0, '
                                    '"production_rate_sp": 100, "a_comp_sp": 47, "rationale": "x"}')])
    t = OllamaOperator(http_post=fake, background=False).decide(make_state())
    assert t.pressure_sp == 2900.0 and "clamped" in t.rationale


def test_ollama_trained_hold_reply_is_a_hold():
    fake = FakeOllama([ollama_reply('{"pressure_sp": null, "level_sp": null, "production_rate_sp": null, '
                                    '"a_comp_sp": null, "rationale": "Pressure or level reading implausible"}')])
    assert is_hold(OllamaOperator(http_post=fake, background=False).decide(make_state(pressure=0.0)))


@pytest.mark.parametrize("r", [
    ConnectionRefusedError("ollama down"), TimeoutError("timed out"),
    {"error": "model 'scram-operator' not found"}, ollama_reply(TRAINED_REPLY, done_reason="length"),
    ollama_reply("not json at all"), ollama_reply('{"pressure_sp": "high"}'), {"message": None}, None,
])
def test_ollama_failure_degrades_to_hold(r):
    t = OllamaOperator(http_post=FakeOllama([r]), background=False).decide(make_state())
    assert is_hold(t) and t.rationale.startswith("hold:")


def test_ollama_scrammed_plant_holds_without_calling_model():
    fake = FakeOllama([ollama_reply(TRAINED_REPLY)])
    t = OllamaOperator(http_post=fake, background=False).decide(make_state(scrammed=True, scram_reason="x"))
    assert is_hold(t) and fake.requests == []


def test_make_policy_ollama_selected_by_env(monkeypatch):
    monkeypatch.setenv("SCRAM_OPERATOR", "ollama")
    monkeypatch.setattr(policy_mod, "ollama_available", lambda *a, **k: True)
    assert isinstance(make_policy(), OllamaOperator)


def test_make_policy_ollama_unavailable_falls_back_to_baseline(monkeypatch):
    monkeypatch.setenv("SCRAM_OPERATOR", "ollama")
    monkeypatch.setattr(policy_mod, "ollama_available", lambda *a, **k: False)
    assert isinstance(make_policy(), BaselineOperator)


def test_ollama_available_is_false_when_server_unreachable():
    assert policy_mod.ollama_available(base_url="http://127.0.0.1:9", timeout_s=0.5) is False
