import json
import subprocess
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from live.engine import Engine
from training import local_gguf_server as server
from training.launch_demo_control_room import control_room_lease


def isolate_server_socket(monkeypatch):
    monkeypatch.setattr(server.socket, "socket", lambda: nullcontext(SimpleNamespace(bind=lambda address: None)))


def test_engine_start_is_idempotent_and_stop_joins_before_closing_socket(monkeypatch):
    engine = Engine(brain="baseline", reset=False)
    entered = threading.Event()
    closed = []

    def run():
        entered.set()
        engine._stop.wait(3)

    monkeypatch.setattr(engine, "_run", run)
    engine.svc = SimpleNamespace(stop=lambda: closed.append(not engine._thread.is_alive()))
    engine.start()
    assert entered.wait(1)
    first = engine._thread
    assert engine.start()._thread is first
    engine.stop()
    assert not first.is_alive() and closed == [True]
    assert engine.status == "stopped"
    with pytest.raises(RuntimeError, match="cannot restart"):
        engine.start()
    with pytest.raises(ValueError, match="stopped"):
        engine.submit("authority", value=1.)


def test_dead_control_loop_rejects_actions_instead_of_queueing_forever():
    engine = Engine(reset=False)
    engine._thread = SimpleNamespace(is_alive=lambda: False)
    with pytest.raises(ValueError, match="Restart"):
        engine.submit("clear_faults")
    assert engine._q.empty()


def test_busy_http_port_does_not_start_a_second_plant_writer(monkeypatch):
    from live import __main__ as live

    started = []
    monkeypatch.setenv("SCRAM_OBSERVER_MODE", "none")
    engine = SimpleNamespace(start=lambda: started.append(True))
    monkeypatch.setattr(live.sys, "argv", ["live", "--brain", "baseline", "--no-reset", "--no-browser"])
    monkeypatch.setattr(live, "plant_up", lambda: True)
    monkeypatch.setattr(live, "Engine", lambda **kwargs: engine)

    def busy(*args):
        raise OSError("port already in use")

    monkeypatch.setattr(live, "serve", busy)
    with pytest.raises(OSError, match="port"):
        live.main()
    assert not started


def test_failed_engine_start_closes_reserved_http_socket_without_shutdown_deadlock(monkeypatch):
    from live import __main__ as live

    events = []
    monkeypatch.setenv("SCRAM_OBSERVER_MODE", "none")

    def fail():
        raise RuntimeError("startup failed")

    engine = SimpleNamespace(start=fail, stop=lambda: events.append("engine stopped"))
    web = SimpleNamespace(server_close=lambda: events.append("socket closed"),
                          shutdown=lambda: events.append("incorrect shutdown"))
    monkeypatch.setattr(live.sys, "argv", ["live", "--brain", "baseline", "--no-browser"])
    monkeypatch.setattr(live, "plant_up", lambda: True)
    monkeypatch.setattr(live, "Engine", lambda **kwargs: engine)
    monkeypatch.setattr(live, "serve", lambda *args: web)
    with pytest.raises(RuntimeError):
        live.main()
    assert events == ["engine stopped", "socket closed"]


def registry(tmp_path):
    folder = tmp_path / "llama-server"
    folder.mkdir()
    value = {"model": str(tmp_path / "weights.gguf"), "sha256": "model-hash", "context": 3072}
    (folder / "registry.json").write_text(json.dumps({"demo": value}))
    return folder


def test_inference_version_failure_never_launches_a_process(monkeypatch, tmp_path):
    registry(tmp_path)
    isolate_server_socket(monkeypatch)
    launched = []

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("version", 10)

    monkeypatch.setattr(server.subprocess, "run", fail)
    monkeypatch.setattr(server.subprocess, "Popen", lambda *args, **kwargs: launched.append(args))
    with pytest.raises(subprocess.TimeoutExpired):
        server.ensure(tmp_path, "demo")
    assert launched == []


def test_unhealthy_inference_process_is_terminated(monkeypatch, tmp_path):
    folder = registry(tmp_path)
    isolate_server_socket(monkeypatch)
    events = []
    process = SimpleNamespace(pid=123, poll=lambda: None, terminate=lambda: events.append("terminate"),
                              wait=lambda timeout: events.append("wait"))
    monkeypatch.setattr(server.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(server.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="version", stderr=""))
    monkeypatch.setattr(server.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 1.))
    monkeypatch.setattr(server, "sha", lambda path: "hash")
    times = iter([0., 100.])
    monkeypatch.setattr(server.time, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError, match="healthy"):
        server.ensure(tmp_path, "demo")
    assert events == ["terminate", "wait"]
    assert json.loads((folder / "process.json").read_text())["create_time"] == 1.


def test_matching_executable_name_does_not_allow_terminating_another_model(monkeypatch, tmp_path):
    folder = registry(tmp_path)
    exe = str(tmp_path / "llama-server.exe")
    (folder / "process.json").write_text(json.dumps({"pid": 123, "command": [exe, "-m", "ours"]}))
    process = SimpleNamespace(cmdline=lambda: [exe, "-m", "someone-elses-model"])
    monkeypatch.setattr(server.psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(server.psutil, "Process", lambda pid: process)
    with pytest.raises(RuntimeError, match="task server"):
        server.ensure(tmp_path, "demo")


def test_occupied_model_port_is_not_adopted_as_a_healthy_task_server(monkeypatch, tmp_path):
    registry(tmp_path)
    launched = []

    def occupied(address):
        raise OSError("port already used")

    monkeypatch.setattr(server.socket, "socket", lambda: nullcontext(SimpleNamespace(bind=occupied)))
    monkeypatch.setattr(server.subprocess, "Popen", lambda *args, **kwargs: launched.append(True))
    with pytest.raises(OSError, match="used"):
        server.ensure(tmp_path, "demo")
    assert not launched


def test_demo_lease_rejects_duplicate_launch_without_replacing_the_live_lock(tmp_path):
    with control_room_lease(tmp_path, port=0) as record:
        with (pytest.raises(RuntimeError, match="already running"),
              control_room_lease(tmp_path, port=record["mutex_port"])):
            pytest.fail("Duplicate lease acquired")
        assert json.loads((tmp_path / "control-room.lock").read_text())["token"] == record["token"]
    assert not (tmp_path / "control-room.lock").exists()


def test_stale_demo_lease_is_preserved_and_recoverable(tmp_path):
    import os

    current = server.psutil.Process(os.getpid())
    (tmp_path / "control-room.lock").write_text(json.dumps({
        "pid": current.pid, "create_time": current.create_time()-1000, "command": current.cmdline()}))
    with control_room_lease(tmp_path, port=0):
        assert len(list(tmp_path.glob("control-room.stale-*.json"))) == 1
    assert not (tmp_path / "control-room.lock").exists()
