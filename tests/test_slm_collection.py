"""Isolation and ownership checks for fresh simulator collection."""
import json
import threading
from itertools import pairwise
from types import SimpleNamespace

import pytest

from training import collect_slm_evidence as collection


def info(port="49152", host="127.0.0.1", label=collection.EXPERIMENT):
    return {"Id": "owned-id", "Config": {"Labels": {"scram.experiment": label}},
            "NetworkSettings": {"Ports": {"55555/tcp": [{"HostIp": host, "HostPort": port}]}}}


@pytest.mark.parametrize("port,host", [("55555", "127.0.0.1"), ("49152", "0.0.0.0"), ("80", "127.0.0.1")])
def test_collector_rejects_app_port_or_public_binding(port, host):
    with pytest.raises(RuntimeError):
        collection.published_port(info(port, host))


def test_cleanup_requires_both_exact_id_and_experiment_label():
    collection.assert_owned(info(), "owned-id")
    with pytest.raises(RuntimeError):
        collection.assert_owned(info(), "different-id")
    with pytest.raises(RuntimeError):
        collection.assert_owned(info(label="user-container"), "owned-id")


def test_default_port_allocation_is_removed_and_retried_before_any_client(tmp_path, monkeypatch):
    calls, started = [], []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "run":
            identity = f"attempt-{len(started)}"
            started.append(identity)
            return identity
        if args[0] == "inspect":
            value = info("55555" if args[1] == "attempt-0" else "49152")
            value["Id"] = args[1]
            return json.dumps([value])
        assert args == ("rm", "--force", "attempt-0")
        return args[-1]

    monkeypatch.setattr(collection, "docker", docker)
    monkeypatch.setattr(collection, "PlantService", lambda **kwargs: pytest.fail("port allocation must not contact a plant"))
    allocation = collection.start_isolated_container("sha256:image")
    assert allocation["container_id"] == "attempt-1" and allocation["port"] == 49152
    assert len(allocation["rejected_allocations"]) == 1
    assert ("rm", "--force", "attempt-0") in calls
    assert ("rm", "--force", "attempt-1") not in calls


def test_forbidden_port_retries_are_bounded_and_every_owned_attempt_is_removed(monkeypatch):
    removed, started = [], []

    def docker(*args, **kwargs):
        if args[0] == "run":
            identity = f"attempt-{len(started)}"
            started.append(identity)
            return identity
        if args[0] == "inspect":
            value = info("55555")
            value["Id"] = args[1]
            return json.dumps([value])
        assert args[:2] == ("rm", "--force")
        removed.append(args[-1])
        return args[-1]

    monkeypatch.setattr(collection, "docker", docker)
    with pytest.raises(RuntimeError, match="all 3 attempts"):
        collection.start_isolated_container("sha256:image", attempts=3)
    assert removed == started and len(started) == 3


def test_scheduler_does_not_abort_or_burst_replay_after_a_host_pause():
    assert collection.next_deadline(1.5, 3.159, .5) == 3.659
    assert collection.next_deadline(1.5, 1.55, .5) == 2


def test_cancelled_work_never_launches_a_simulator(tmp_path, monkeypatch):
    event = threading.Event()
    event.set()
    calls = []
    monkeypatch.setattr(collection, "docker", lambda *args: calls.append(args))
    with pytest.raises(InterruptedError):
        collection.run_recording(tmp_path, {}, {}, "image-id", event)
    assert calls == []


def test_duplicate_collector_cannot_overwrite_an_active_recording(tmp_path, monkeypatch):
    plan = collection.ensure_plan(tmp_path)
    spec = plan["recordings"][0]
    directory = tmp_path / "raw/train"
    directory.mkdir(parents=True)
    lock = directory / f"{spec['recording_id']}.lock"
    lock.write_text('{"pid":123,"token":"other-process"}')
    calls = []
    monkeypatch.setattr(collection, "docker", lambda *args: calls.append(args))
    with pytest.raises(FileExistsError):
        collection.run_recording(tmp_path, plan, spec, "image-id")
    assert calls == []
    assert json.loads(lock.read_text())["token"] == "other-process"


def test_plan_assigns_disjoint_groups_and_never_changes_an_existing_plan(tmp_path):
    plan = collection.ensure_plan(tmp_path)
    ids = [r["recording_id"] for r in plan["recordings"]]
    assert len(ids) == len(set(ids)) == 64
    assert {r["split"] for r in plan["recordings"]} == {"train", "val", "sealed"}
    assert collection.ensure_plan(tmp_path) == plan
    path = tmp_path / "collection-plan.json"
    plan["seed"] += 1
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="differs"):
        collection.ensure_plan(tmp_path)


def test_excursion_supplement_preserves_initial_allocation_and_uses_new_groups(tmp_path):
    initial = collection.ensure_plan(tmp_path)
    original = (tmp_path / "collection-plan.json").read_bytes()
    supplement = collection.ensure_excursion_supplement(tmp_path)
    assert (tmp_path / "collection-plan.json").read_bytes() == original
    assert len(supplement["recordings"]) == 16
    assert not {s["recording_id"] for s in initial["recordings"]} & {s["recording_id"] for s in supplement["recordings"]}
    assert all(s["driver"] == "near_limit_pressure_plateau_v2" and 2865 <= s["pressure_target"] <= 2900
               for s in supplement["recordings"])
    records, hashes = collection.collection_plans(tmp_path)
    assert len(records) == 80 and len(hashes) == 2


def test_pressure_fixture_vents_more_as_measured_pressure_rises():
    low = collection.pressure_plateau_command(SimpleNamespace(pressure=2840, level=44.18), 2880)
    high = collection.pressure_plateau_command(SimpleNamespace(pressure=2900, level=44.18), 2880)
    assert low.purge_valve == 0 and high.purge_valve > low.purge_valve
    assert high.f1_valve == low.f1_valve == 55
    with pytest.raises(ValueError, match="pressure band"):
        collection.pressure_plateau_command(SimpleNamespace(pressure=2800, level=44), 2820)


def test_failed_native_interface_probe_removes_only_task_owned_container(tmp_path, monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "run":
            assert "--publish" in args and "127.0.0.1::55555" in args
            assert "--entrypoint" in args
            return "owned-id"
        if args[0] == "inspect":
            return json.dumps([info()])
        if args[0] == "exec":
            return "a"*64+"  /app/simulation/simulation"
        assert args == ("rm", "--force", "owned-id")
        return "owned-id"

    class Service:
        def __init__(self, *, port):
            assert port == 49152
            self.client = self

        def start(self):
            return self

        def read(self):
            return {"outputs": {"pressure": 2700}}

        def stop(self):
            calls.append(("client-closed",))

    monkeypatch.setattr(collection, "docker", docker)
    monkeypatch.setattr(collection, "PlantService", Service)
    plan = collection.ensure_plan(tmp_path)
    with pytest.raises(RuntimeError, match="native measured/truth"):
        collection.run_recording(tmp_path, plan, plan["recordings"][0], "sha256:image-id")
    assert ("client-closed",) in calls
    assert calls[-1] == ("rm", "--force", "owned-id")
    assert not list((tmp_path / "raw/train").glob("*.completed.json"))


def test_full_collector_finishes_after_pause_and_retains_gap_for_quarantine(tmp_path, monkeypatch):
    from training.slm_evidence_data import telemetry_row

    class Clock:
        current = 0.0
        paused = False

        def monotonic(self):
            return self.current

        def sleep(self, duration):
            self.current += duration

    clock = Clock()
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "run":
            return "owned-id"
        if args[0] == "inspect":
            return json.dumps([info()])
        if args[0] == "exec":
            return "a"*64+"  /app/simulation/simulation"
        assert args == ("rm", "--force", "owned-id")
        return "owned-id"

    measured = {"pressure": 2700, "level": 44.18, "f1_flow": 201, "f2_flow": 5.6,
                "purge_flow": 7, "product_flow": 100, "a_in_purge": .47,
                "b_in_purge": .14, "c_in_purge": .39, "scrammed": False,
                "f1_valve_pos": 31.45, "f2_valve_pos": 13.24,
                "purge_valve_pos": .3926, "product_valve_pos": 47.03}

    class Client:
        def __init__(self):
            self.inputs = {}
            self.position = 31.45

        def write_inputs(self, values):
            self.inputs.update(values)
            if "f1_valve_sp" in values and not self.inputs.get("f1_stuck"):
                self.position = values["f1_valve_sp"]

        def read(self):
            return {"outputs": {"pressure": 2700},
                    "measured": {"pressure": 0 if self.inputs.get("tank_pressure_fault_mode") == 4 else 2700},
                    "state": {"f1_valve_pos": self.position}}

    class Service:
        def __init__(self, *, port):
            assert port == 49152
            self.client = Client()

        def start(self):
            return self

        def read(self):
            return SimpleNamespace(**measured, truth={"pressure": 2700})

        def apply(self, command):
            if clock.current >= 1.5 and not clock.paused:
                clock.current += 1.659
                clock.paused = True
            clock.current += .01
            return self.read()

        def stop(self):
            calls.append(("client-closed",))

    monkeypatch.setattr(collection, "docker", docker)
    monkeypatch.setattr(collection, "PlantService", Service)
    monkeypatch.setattr(collection.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(collection.time, "sleep", clock.sleep)
    monkeypatch.setattr(collection, "measured_dict", lambda state: dict(measured))
    plan = collection.make_plan()
    spec = plan["recordings"][0]
    spec["duration_s"] = 20
    (tmp_path / "collection-plan.json").write_text(json.dumps(plan))
    result = collection.run_recording(tmp_path, plan, spec, "sha256:image-id")
    assert result["complete"] and result["native_stuck_probe_passed"]
    records = [json.loads(line) for line in (tmp_path / "raw/train/train-normal-00.jsonl").read_text().splitlines()]
    assert len(records) >= 31
    assert max(b["t"]-a["t"] for a, b in pairwise(records)) > 1.5
    row, issue = telemetry_row(records[:31:2], spec, result["sha256"])
    assert row is None and issue == "misaligned_sample_timing"
    assert calls[-1] == ("rm", "--force", "owned-id")
    assert not (tmp_path / "raw/train/train-normal-00.lock").exists()
