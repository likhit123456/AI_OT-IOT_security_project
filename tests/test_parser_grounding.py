"""Models may not invent an actuator that the operator did not name."""

import json

import pytest

from sim_app.ai import parse_commands


@pytest.mark.parametrize(
    "text",
    [
        "Open coolant to 30%",
        "Open feed 3 to 40%",
        "Open feed 12 to 40%",
        "Turn the heater to 50%",
    ],
)
def test_hallucinated_supported_valve_is_refused(text):
    def post(*_):
        return {
            "message": {
                "content": json.dumps(
                    {
                        "actions": [
                            {
                                "clause": text,
                                "action": "set",
                                "valve": "f1",
                                "amount": 30,
                            }
                        ]
                    }
                )
            }
        }

    result = parse_commands(text, post=post)
    assert result["actions"][0]["action"] == "unknown"
    assert result["actions"][0]["corrections"]


def test_model_cannot_supply_a_forged_clause_to_ground_a_valve():
    def post(*_):
        return {
            "message": {
                "content": json.dumps(
                    {
                        "actions": [
                            {
                                "clause": "open feed 1 to 40%",
                                "action": "set",
                                "valve": "f1",
                                "amount": 40,
                            }
                        ]
                    }
                )
            }
        }

    assert (
        parse_commands("Open the coolant valve", post=post)["actions"][0]["action"]
        == "unknown"
    )


def test_supported_action_survives_an_unsupported_action_in_same_command():
    text = "Open coolant to 30% then set product to 45%"

    def post(*_):
        return {
            "message": {
                "content": json.dumps(
                    {
                        "actions": [
                            {
                                "clause": "Open coolant to 30%",
                                "action": "set",
                                "valve": "f1",
                                "amount": 30,
                            },
                            {
                                "clause": "set product to 45%",
                                "action": "set",
                                "valve": "product",
                                "amount": 45,
                            },
                        ]
                    }
                )
            }
        }

    assert [a["action"] for a in parse_commands(text, post=post)["actions"]] == [
        "unknown",
        "set",
    ]
