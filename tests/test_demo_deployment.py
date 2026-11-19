import json
from types import SimpleNamespace

import pytest

from training import launch_demo_control_room as launch
from training.demo_observer_service import owned_process
from training.freeze_demo_observer import freeze
from training.shared_slm_data import sha


def test_model_tampering_is_rejected_before_application_start(tmp_path):
    folder = tmp_path / "app-deployment"
    folder.mkdir()
    weights = folder / "model.gguf"
    weights.write_bytes(b"original")
    manifest = {"alias": launch.ALIAS, "schema": "measured-evidence-v2", "demo_only": True,
                "weights_file": weights.name, "weights_sha256": sha(weights)}
    (folder / "deployment.json").write_text(json.dumps(manifest))
    weights.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash changed"):
        launch.deployment(tmp_path)


def test_manifest_cannot_escape_deployment_directory(tmp_path):
    folder = tmp_path / "app-deployment"
    folder.mkdir()
    (folder / "deployment.json").write_text(json.dumps({
        "alias": launch.ALIAS, "schema": "measured-evidence-v2", "demo_only": True,
        "weights_file": "../other.gguf"}))
    with pytest.raises(ValueError, match="identity or schema"):
        launch.deployment(tmp_path)


@pytest.mark.parametrize("different_command,different_time", [(True, False), (False, True)])
def test_stop_refuses_reused_or_unrelated_process(monkeypatch, different_command, different_time):
    record = {"pid": 123, "command": ["our-server.exe", "serve"], "create_time": 20}
    process = SimpleNamespace(
        cmdline=lambda: ["other-server.exe"] if different_command else record["command"],
        create_time=lambda: 21 if different_time else 20)
    monkeypatch.setattr(launch.psutil, "Process", lambda pid: process)
    with pytest.raises(RuntimeError, match="another process|reused"):
        launch.checked_process(record)
    with pytest.raises(RuntimeError, match="task-owned"):
        owned_process(record)


def test_demo_settings_are_explicit_and_do_not_mutate_environment(monkeypatch):
    monkeypatch.setenv("SCRAM_OBSERVER_MODE", "legacy")
    settings = launch.settings()
    assert settings["SCRAM_OBSERVER_MODE"] == "unified"
    assert settings["SCRAM_OBSERVER_SCHEMA"] == "measured-evidence-v2"
    assert settings["SCRAM_OBSERVER_MODEL"] == launch.ALIAS
    assert launch.os.environ["SCRAM_OBSERVER_MODE"] == "legacy"


def test_default_live_start_manages_shared_runner_and_restores_environment(monkeypatch):
    from contextlib import nullcontext
    from live import __main__ as live
    events = []
    monkeypatch.delenv("SCRAM_OBSERVER_MODE", raising=False)
    monkeypatch.setattr(live.sys, "argv", ["live", "--no-browser"])
    monkeypatch.setattr(launch, "control_room_lease", lambda: nullcontext())
    monkeypatch.setattr(launch, "start", lambda: {"pid": 123})
    monkeypatch.setattr(launch, "stop", lambda: events.append("stopped"))

    def execute():
        assert live.os.environ["SCRAM_OBSERVER_MODEL"] == launch.ALIAS
        assert live.os.environ["SCRAM_OBSERVER_EVIDENCE_REVIEW"] == "1"
        raise RuntimeError("simulated startup failure")

    monkeypatch.setattr(live, "_main", execute)
    with pytest.raises(RuntimeError, match="simulated startup"):
        live.main()
    assert events == ["stopped"]
    assert "SCRAM_OBSERVER_MODE" not in live.os.environ


def test_once_frozen_demo_cannot_be_reselected(tmp_path):
    frozen = tmp_path / "freeze.json"
    frozen.write_text('{"preserved":true}')
    with pytest.raises(FileExistsError, match="already frozen"):
        freeze(tmp_path)
    assert frozen.read_text() == '{"preserved":true}'
    assert not (tmp_path / "final-data.jsonl").exists()


def test_inconsistent_training_selection_cannot_open_holdout(tmp_path):
    (tmp_path / "selected.json").write_text('{"checkpoint":"new"}')
    (tmp_path / "completed.json").write_text('{"selected":{"checkpoint":"old"}}')
    with pytest.raises(ValueError, match="not complete"):
        freeze(tmp_path)
    assert not (tmp_path / "freeze.json").exists()
    assert not (tmp_path / "final-data.jsonl").exists()
