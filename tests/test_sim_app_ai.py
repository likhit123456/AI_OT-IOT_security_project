"""Fast tests for sim_app/ai.py: command parsing (Ollama mocked) and the rescue regulator."""
import json

import pytest

from contracts import SupervisoryTargets
from sim_app.ai import DESIGN_VALVES, parse_command, plant_state, regulate


def fake(reply: dict):
    def post(url, body, timeout_s):
        assert body["model"] == "qwen2.5:1.5b" and body["format"]["type"] == "object"
        return {"message": {"content": json.dumps(reply)}}
    return post


@pytest.mark.parametrize("text,model,want", [
    ("increase feed 1 by 5%", {"action": "adjust", "valve": "f1", "amount": 5}, ("adjust", "f1", 5)),
    ("open feed 2 to 60%", {"action": "adjust", "valve": "f2", "amount": 60}, ("set", "f2", 60)),
    ("put the purge at 15%", {"action": "adjust", "valve": "purge", "amount": -5}, ("set", "purge", 15)),
    ("reduce feed 2 by 4", {"action": "set", "valve": "f2", "amount": 4}, ("adjust", "f2", -4)),
    ("close product by 3", {"action": "adjust", "valve": "product", "amount": 3}, ("adjust", "product", -3)),
    ("cut purge to 0", {"action": "set", "valve": "purge", "amount": 0}, ("set", "purge", 0)),
    ("save the reactor", {"action": "rescue", "valve": None, "amount": None}, ("rescue", None, None)),
    ("stop the AI", {"action": "stop", "valve": "f1", "amount": 3}, ("stop", None, None)),
    ("open the coolant", {"action": "adjust", "valve": "coolant", "amount": 5}, ("unknown", "coolant", 5)),
])
def test_parse_command(text, model, want):
    r = parse_command(text, post=fake(model))
    assert (r["action"], r["valve"], r["amount"]) == want
    corrected = (model.get("action"), model.get("amount")) != (want[0], want[2]) and want[0] in ("adjust", "set")
    assert bool(r["corrections"]) == corrected


def test_parse_command_bad_json_is_unknown():
    r = parse_command("x", post=lambda u, b, t: {"message": {"content": "not json"}})
    assert r["action"] == "unknown"


def state(p, lvl, raw_extra=None):
    raw = {"measured": {"pressure": p, "liquid_level": lvl}, "state": {}, "outputs": {}}
    return plant_state(raw)


def test_regulator_high_pressure_opens_outlets_and_cuts_feeds():
    t = SupervisoryTargets(pressure_sp=2600.6, level_sp=44.18, production_rate_sp=70.0, a_comp_sp=47.0)
    v = regulate(t, state(3050.0, 44.18))
    assert v["purge"] == 100.0                                   # 450 kPa over -> fully open
    assert v["f1"] < DESIGN_VALVES["f1"] * 0.5 and v["f2"] < DESIGN_VALVES["f2"] * 0.5
    assert v["product"] > DESIGN_VALVES["product"]


def test_regulator_at_design_point_returns_design_lineup():
    t = SupervisoryTargets(pressure_sp=2700.6, level_sp=44.18, production_rate_sp=100.0, a_comp_sp=47.0)
    v = regulate(t, state(2700.6, 44.18))
    assert v == pytest.approx(DESIGN_VALVES, abs=1e-6)


def test_regulator_low_pressure_shuts_purge_and_raises_feeds():
    t = SupervisoryTargets(pressure_sp=2700.6, level_sp=44.18, production_rate_sp=100.0, a_comp_sp=47.0)
    v = regulate(t, state(2500.0, 44.18))
    assert v["purge"] == 0.0 and v["f1"] > DESIGN_VALVES["f1"]


def test_regulator_level_high_opens_product():
    t = SupervisoryTargets(pressure_sp=2700.6, level_sp=44.18, production_rate_sp=100.0, a_comp_sp=47.0)
    assert regulate(t, state(2700.6, 50.0))["product"] > DESIGN_VALVES["product"]
    assert regulate(t, state(2700.6, 40.0))["product"] < DESIGN_VALVES["product"]


def test_regulator_hold_when_model_holds():
    assert regulate(SupervisoryTargets(), state(0.0, 44.0)) is None
