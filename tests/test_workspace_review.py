"""Regression checks for the workspace review's input and session fixes."""
import math

import pytest
from pydantic import ValidationError

from live.engine import Engine
from sim_app.server import Manipulation


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_authority_does_not_change_operator_cap(value):
    engine = Engine(reset=False)
    engine.authority = 0.25
    with pytest.raises(ValueError, match="finite"):
        engine.submit("authority", value=value)
    assert engine.authority == 0.25


@pytest.mark.parametrize("field", ["delta", "value"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_manual_move_is_rejected(field, value):
    with pytest.raises(ValidationError, match="finite"):
        Manipulation(variable="f1", **{field: value})


def test_run_identity_is_stable_for_polling_and_changes_after_restart():
    first, second = Engine(reset=False), Engine(reset=False)
    assert first.read()["run_id"] == first.read()["run_id"]
    assert first.read()["run_id"] != second.read()["run_id"]


def test_connection_failure_overrides_last_healthy_snapshot():
    engine = Engine(reset=False)
    engine.snapshot_data = {"status": "running", "error": "", "t": 10}
    engine.status = "plant connection lost; reconnecting"
    engine.error = "socket closed"
    assert engine.read()["status"] == engine.status
    assert engine.read()["error"] == "socket closed"
