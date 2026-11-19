from types import SimpleNamespace

import pytest

from l0_plant.plant_service import PlantService
from live.engine import Engine


@pytest.mark.parametrize("reset", [False, True])
def test_transient_startup_failure_retries_without_repeating_reset(monkeypatch, reset):
    engine = Engine(brain="baseline", reset=reset)
    attempts = []
    closed = []
    engine.svc = SimpleNamespace(stop=lambda: closed.append(True))
    monkeypatch.setattr(engine, "_make_brain", lambda kind: None)
    monkeypatch.setattr(engine._stop, "wait", lambda delay: False)

    def connect(requested_reset):
        attempts.append(requested_reset)
        if len(attempts) == 1:
            raise ConnectionError("simulator closed the connection")
        engine.status = "running"
        engine._stop.set()

    monkeypatch.setattr(engine, "_connect", connect)
    engine._run()
    assert attempts == [reset, False]
    assert closed == [True]
    assert engine.status == "running"
    assert engine.error == ""


def test_stop_interrupts_startup_retry(monkeypatch):
    engine = Engine(brain="baseline", reset=False)
    calls = []
    monkeypatch.setattr(engine, "_make_brain", lambda kind: None)
    monkeypatch.setattr(engine._stop, "wait", lambda delay: True)

    def connect(reset):
        calls.append(reset)
        raise ConnectionError("not ready")

    monkeypatch.setattr(engine, "_connect", connect)
    engine._run()
    assert calls == [False]


def test_failed_gate_seed_closes_partial_plant_connection():
    closed = []
    service = PlantService(gate=SimpleNamespace(last_good=None, seed=lambda state: None))

    def read():
        raise ConnectionError("simulator closed the connection")

    service.client = SimpleNamespace(connect=lambda: None, read=read, close=lambda: closed.append(True))
    with pytest.raises(ConnectionError):
        service.start()
    assert closed == [True]
    assert service._connected is False
