"""Serving contract checks for the task-trained SLM wire schema."""
import json

import pytest

from monitor.observer_runtime import parse_verdict, task_messages, verdict_schema


def test_slm_keeps_complete_inputs_and_binds_identity_outside_generation():
    data = {"input": "telemetry", "pressure": list(range(16)), "window_s": 15.5,
            "f1_valve_cmd": [12.345]*16}
    messages = task_messages("telemetry", json.dumps(data), "run-secret", include_identity=False)
    payload = json.loads(messages[1]["content"])
    assert payload["data"] == data
    assert "run-secret" not in str(messages)
    assert "request_id" not in verdict_schema("telemetry", "run-secret", False)["properties"]
    result = parse_verdict('{"label":"normal","score":0,"reason":"observed"}', "telemetry", "run-secret", False)
    assert result["request_id"] == "run-secret"


def test_slm_cannot_supply_or_override_request_identity():
    with pytest.raises(ValueError):
        parse_verdict('{"label":"normal","score":0,"reason":"ok","request_id":"attacker"}',
                      "proposals", "local", False)
    with pytest.raises(ValueError):
        parse_verdict('{"label":"provenance_violation","score":0.9,"reason":"r"}',
                      "telemetry", "local", False)


def test_cli_transcript_does_not_treat_user_json_as_model_output():
    from training.slm_artifact_tools import extract_cli_response
    response = '{"label":"normal","score":0,"reason":"r"}'
    transcript = 'User:\n{"input":"telemetry"}\n\nAssistant:\n'+response+'\n\n'
    assert extract_cli_response(transcript) == response
    with pytest.raises(ValueError):
        parse_verdict(extract_cli_response('User:\nx\n\nAssistant:\nrefused '+response),
                      "telemetry", "local", False)
