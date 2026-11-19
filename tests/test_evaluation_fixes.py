"""Offline regression tests for the November project evaluation findings."""
import io
import json
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from live import commands
from live.engine import Engine, RunChangedError
from live.server import make_handler
from l0_plant.owner import PlantOwner
from sim_app.server import Plant


@pytest.mark.parametrize("text", [
    "do not open feed 1", "do not release the valves", "do not raise pressure by 50",
    "don't open feed 1", "DON’T close the purge", "never release", "no feed 1 to 100%",
    "open feed 2 but do not open feed 1", "open feed 1 unless pressure is high",
    "if pressure falls open feed 1", "should I open feed 1", "open feed 1?",
    "avoid opening feed 1", "stop opening feed 1", "open or close feed 1",
    "open feed 1 and close feed 1",
])
def test_unclear_command_is_rejected_before_any_queue_or_model_dispatch(text):
    engine = Engine(reset=False)
    with pytest.raises(ValueError):
        engine.submit("chat", text=text)
    assert engine._q.empty()
    assert not engine.chat and not engine.manual


@pytest.mark.parametrize("text,kind", [
    ("open feed 1", "valves"), ("release", "release"),
    ("raise pressure by 50", "setpoints"), ("hold design point", "setpoints"),
    ("close purge and feed 1 to 20%", "valves"),
])
def test_affirmative_commands_remain_supported(text, kind):
    assert commands.parse(text)["kind"] == kind


def request(engine, body, headers=None):
    cls = make_handler(engine)
    handler = cls.__new__(cls)
    data = json.dumps(body).encode()
    handler.path = "/api/action"
    handler.server = SimpleNamespace(server_port=8765)
    handler.headers = {"Host": "127.0.0.1:8765", "Content-Type": "application/json",
                       "Content-Length": str(len(data)), **(headers or {})}
    handler.rfile, handler.wfile = io.BytesIO(data), io.BytesIO()
    result = {}
    handler.send_response = lambda code: result.update(status=code)
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.do_POST()
    return result["status"], json.loads(handler.wfile.getvalue())


@pytest.mark.parametrize("action,kwargs", [("forged", {}), ("authority", {"value": .1})])
def test_reset_between_http_read_and_submit_rejects_old_request(action, kwargs):
    engine = Engine(reset=False)
    old = engine.run_id

    def snapshot(*_):
        engine.run_id = "new-run"  # deterministic reset between HTTP read and submit
        return {"run_id": old, "telemetry_stale": False}

    engine.read = snapshot
    code, _ = request(engine, {"action": action, "expected_run_id": old, **kwargs})
    assert code == 409 and engine._q.empty() and engine.authority == 1


@pytest.mark.parametrize("action,kwargs", [("forged", {}), ("authority", {"value": .1})])
def test_reset_during_validation_cancels_all_action_types(action, kwargs):
    engine = Engine(reset=False)
    old = engine.run_id
    setattr(engine, f"_check_{action}", lambda **_: setattr(engine, "run_id", "new-run"))
    assert engine.submit(action, expected_run_id=old, **kwargs) == "queued"
    engine._q.get_nowait()()
    assert not engine.scenarios and engine.authority == 1
    with pytest.raises(RunChangedError):
        engine.submit(action, expected_run_id=old, **kwargs)


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "https://unrelated.example", "Sec-Fetch-Site": "cross-site"}, 403),
    ({"Origin": "null"}, 403),
    ({"Host": "attacker.example:8765", "Origin": "http://attacker.example:8765"}, 403),
    ({"Origin": "http://127.0.0.1:8765.evil.example"}, 403),
    ({"Origin": "http://localhost:9999"}, 403),
    ({"Content-Type": "text/plain"}, 415),
    ({"Content-Type": ""}, 415),
    ({"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"}, 200),
    ({"Host": "localhost:8765", "Origin": "http://localhost:8765"}, 200),
    ({"Host": "127.0.0.1:5173", "Origin": "http://127.0.0.1:5173"}, 200),
    ({"Host": "localhost:5173", "Origin": "http://localhost:5173"}, 200),
    ({"Content-Type": "application/json; charset=utf-8"}, 200),
    ({}, 200),  # local non-browser clients need no Origin header
])
def test_action_browser_boundary(headers, status):
    calls = []
    engine = SimpleNamespace(read=lambda *_: {"run_id": "run"},
                             submit=lambda *a, **kw: calls.append((a, kw)) or "queued")
    assert request(engine, {"action": "clear_faults"}, headers)[0] == status
    assert bool(calls) == (status == 200)


def test_shared_owner_rejects_both_apps_before_any_second_start(monkeypatch):
    engine = Engine(reset=True, port=59997)
    entered = threading.Event()
    monkeypatch.setattr(engine, "_run", lambda: (entered.set(), engine._stop.wait(3)))
    plant = Plant(host="localhost", port=59997)
    second = Engine(reset=True, port=59997)
    writes = []
    monkeypatch.setattr(plant, "_poll", lambda: writes.append("manual"))
    monkeypatch.setattr(second, "_run", lambda: writes.append("second control room"))
    engine.start()
    try:
        assert entered.wait(1)
        with pytest.raises(RuntimeError, match="already has a writer"):
            plant.start()
        with pytest.raises(RuntimeError, match="already has a writer"):
            second.start()
        assert not writes
    finally:
        engine.stop()
    # Reverse direction: manual studio owns the endpoint, control room refuses.
    monkeypatch.setattr(plant, "_poll", lambda: plant._stop_poll.wait(3))
    plant.start()
    try:
        with pytest.raises(RuntimeError, match="already has a writer"):
            second.start()
    finally:
        plant.stop()
    owner = PlantOwner("127.0.0.1", 59997)
    owner.acquire()
    owner.release()


def test_lease_is_cross_process_and_released_after_process_exit():
    owner = PlantOwner("127.0.0.1", 59998)
    owner.acquire()
    script = "from l0_plant.owner import PlantOwner; o=PlantOwner('localhost',59998); o.acquire()"
    try:
        blocked = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)
        assert blocked.returncode != 0 and "already has a writer" in blocked.stderr
    finally:
        owner.release()
    assert subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=10).returncode == 0
    owner.acquire()  # child exit released its lease without explicit cleanup
    owner.release()


def test_shutdown_retains_manual_lease_until_inflight_reset_finishes(monkeypatch):
    from fastapi import HTTPException
    from sim_app import server

    plant = Plant(port=59996)
    entered, finish = threading.Event(), threading.Event()
    monkeypatch.setattr(plant, "_poll", lambda: plant._stop_poll.wait(3))

    def settle():
        entered.set()
        assert finish.wait(3)
        return SimpleNamespace(pressure=2700, level=44, cycles_to_settle=1)

    monkeypatch.setattr(server, "reset_and_settle", settle)
    errors = []

    def reset():
        try:
            plant.reset()
        except HTTPException as exc:
            errors.append(exc.status_code)

    plant.start()
    reset_thread = threading.Thread(target=reset)
    stop_thread = threading.Thread(target=plant.stop)
    owner = PlantOwner("localhost", 59996)
    try:
        reset_thread.start()
        assert entered.wait(1)
        stop_thread.start()
        assert plant._stop_poll.wait(1)
        with pytest.raises(RuntimeError, match="already has a writer"):
            owner.acquire()
    finally:
        finish.set()
        reset_thread.join(3)
        stop_thread.join(3)
    assert not reset_thread.is_alive() and not stop_thread.is_alive()
    assert errors == [500]  # reset's final read is cancelled by shutdown, without connecting
    owner.acquire()
    owner.release()
    with pytest.raises(HTTPException, match="stopped"):
        plant.reset()
