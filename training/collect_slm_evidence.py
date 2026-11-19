"""Collect new observer recordings in task-owned, isolated Docker containers.

Never connects to the app's port 55555 or invokes compose/reset_plant. Each
recording starts a fresh simulator with an automatically allocated loopback
port. Controller commands, measured states and evaluation-only truth are stored
separately. Run in normal PowerShell when the sandbox cannot access Docker.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from contracts import ControlCommand, SupervisoryTargets
from l0_plant.plant_service import PlantService
from l1_stub import L1ControlStub
from l3_brain.prompt_format import measured_dict
from training.shared_slm_data import ROOT, sha

RUN = ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2"
EXPERIMENT = "slm-evidence-v2-20261108"
IMAGE = "fortiphyd/grfics-simulation"
VALVES = ("f1", "f2", "purge", "product")


class RejectedPublishedPort(RuntimeError):
    """A Docker-selected host port is forbidden for experiment clients."""


def docker(*args, timeout=30):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"docker {' '.join(args[:2])}: {result.stderr.strip()}")
    return result.stdout.strip()


def make_plan():
    """Fixed allocation before collection; all variants retain their group split."""
    rng = random.Random(3408)
    specs = []
    # Eight train groups allow diversity without duplicating examples. Four
    # validation/test groups provide margin when ambiguous windows are excluded.
    for split, count in (("train", 8), ("val", 4), ("sealed", 4)):
        for label in ("normal", "excursion", "sensor_fault", "actuator_fault"):
            for index in range(count):
                identity = f"{split}-{label}-{index:02d}"
                inputs = {}
                if label == "sensor_fault":
                    channels = ("tank_pressure", "tank_level", "f1_flow", "product_flow")
                    channel = channels[index % len(channels)]
                    mode = (1, 4, 3, 2)[index % 4]
                    severity = {1: 0, 4: .3, 3: 20, 2: 600}[mode]
                    if channel == "tank_level" and mode in (2, 3):
                        severity /= 50
                    inputs = {f"{channel}_fault_mode": mode, f"{channel}_fault_severity": severity}
                elif label == "actuator_fault":
                    inputs = {f"{VALVES[index % 4]}_stuck": 1}
                specs.append({"recording_id": identity, "split": split, "planned_class": label,
                    "duration_s": 210, "fault_on_s": 30, "sample_s": .5,
                    "production_step": rng.uniform(8, 20)*(-1 if index % 2 else 1),
                    "pressure_step": rng.uniform(10, 35)*(-1 if index % 2 else 1),
                    "fault_inputs": inputs})
    return {"experiment": EXPERIMENT, "seed": 3408, "image": IMAGE,
            "strategy": "fresh isolated process per recording; whole-recording split assigned before collection",
            "controller": "existing L1 stub with per-instance biases calibrated to the settled design lineup",
            "calibrated_valves": {"f1": 31.45, "f2": 13.24, "purge": .3926, "product": 47.03},
            "native_stuck_excitation_pp": {"f1": 10, "f2": 8, "purge": 5, "product": 10},
            "native_simulator_required": True, "ambiguous_windows": "quarantine, do not relabel automatically",
            "estimated_sequential_wall_hours": sum(s["duration_s"] for s in specs)/3600,
            "recordings": specs}


def ensure_plan(destination):
    path = destination / "collection-plan.json"
    plan = make_plan()
    destination.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != plan:
            raise ValueError("saved collection plan differs; use a new experiment directory")
    else:
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def make_excursion_supplement():
    """Additional disjoint groups declared after train/val fixture calibration.

    The initial 64-case plan and all completed recordings remain unchanged.
    No model has been trained and no sealed measurements informed this protocol.
    """
    initial = make_plan()
    rng = random.Random(3409)
    specs = []
    for split, count in (("train", 8), ("val", 4), ("sealed", 4)):
        for index in range(count):
            specs.append({"recording_id": f"{split}-excursion-r2-{index:02d}", "split": split,
                "planned_class": "excursion", "duration_s": 330, "fault_on_s": 30,
                "sample_s": .5, "production_step": 0, "pressure_step": 0, "fault_inputs": {},
                "driver": "near_limit_pressure_plateau_v2", "pressure_target": round(rng.uniform(2865, 2900), 3)})
    return {"experiment": EXPERIMENT, "seed": 3409, "image": IMAGE,
            "plan_file": "excursion-supplement-plan.json", "recordings": specs,
            "calibrated_valves": initial["calibrated_valves"],
            "native_stuck_excitation_pp": initial["native_stuck_excitation_pp"],
            "reason": "Train/validation calibration found the original fixture settled near 2820 kPa; keep the 2850 kPa label criterion and add independently allocated native recordings.",
            "sealed_measurements_used_for_design": False,
            "estimated_sequential_wall_hours": sum(s["duration_s"] for s in specs)/3600}


def ensure_excursion_supplement(destination):
    path = destination / "excursion-supplement-plan.json"
    plan = make_excursion_supplement()
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != plan:
            raise ValueError("saved excursion supplement changed; use a new versioned plan")
    else:
        if (destination / "freeze.json").exists() or (destination / "training/config.json").exists():
            raise ValueError("cannot add collection groups after model training/freeze")
        destination.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def collection_plans(directory):
    """Return exact per-group source-plan identities without reading raw traces."""
    paths = [directory / "collection-plan.json"]
    supplement = directory / "excursion-supplement-plan.json"
    if supplement.exists():
        paths.append(supplement)
    records, hashes, seen = [], {}, set()
    for path in paths:
        digest = sha(path)
        hashes[path.name] = digest
        plan = json.loads(path.read_text(encoding="utf-8"))
        for spec in plan["recordings"]:
            if spec["recording_id"] in seen:
                raise ValueError("recording identity crosses collection plans")
            seen.add(spec["recording_id"])
            records.append((spec, digest))
    return records, hashes


def pressure_plateau_command(state, target):
    """Collection fixture only: measured feedback vents a native high-pressure run.

    This does not alter L1 or the production SIS. The level trim keeps the
    fixture away from an unrelated inventory excursion during the pressure run.
    """
    if not 2865 <= target <= 2900:
        raise ValueError("supplement target is outside the declared pressure band")
    return ControlCommand(f1_valve=55, f2_valve=22,
        purge_valve=.3926+.4*(state.pressure-target),
        product_valve=47.03+3*(state.level-44.18)).clamped()


def assert_owned(info, container_id):
    if info["Id"] != container_id or info["Config"].get("Labels", {}).get("scram.experiment") != EXPERIMENT:
        raise RuntimeError("refusing to manage a container without matching experiment ownership")


def published_port(info):
    entries = info["NetworkSettings"]["Ports"]["55555/tcp"]
    if len(entries) != 1 or entries[0]["HostIp"] != "127.0.0.1":
        raise RuntimeError("simulator must expose exactly one loopback-only port")
    port = int(entries[0]["HostPort"])
    if not 1024 <= port <= 65535 or port == 55555:
        raise RejectedPublishedPort("refusing app/default plant port")
    return port


def start_isolated_container(image_id, attempts=8):
    """Reject and remove unsafe allocations before constructing a plant client."""
    rejected = []
    for _ in range(attempts):
        name = f"scram-slm-{uuid.uuid4().hex[:16]}"
        container_id = docker("run", "--pull=never", "--detach", "--name", name,
            "--label", f"scram.experiment={EXPERIMENT}", "--publish", "127.0.0.1::55555",
            "--entrypoint", "/app/simulation/simulation", image_id)
        try:
            info = json.loads(docker("inspect", container_id))[0]
            assert_owned(info, container_id)
            port = published_port(info)
        except BaseException as exc:
            # Validate both identity and label immediately before removing only
            # the container we just launched. Never connect to its rejected port.
            owned = json.loads(docker("inspect", container_id))[0]
            assert_owned(owned, container_id)
            docker("rm", "--force", container_id)
            if not isinstance(exc, RejectedPublishedPort):
                raise
            rejected.append({"container_id": container_id, "reason": str(exc)})
            continue
        return {"container_id": container_id, "port": port, "rejected_allocations": rejected}
    raise RuntimeError(f"Docker selected forbidden host ports in all {attempts} attempts; all owned containers removed: {rejected}")


def next_deadline(deadline, sampled_at, sample_s):
    """Preserve cadence when timely; skip missed slots instead of burst replay."""
    upcoming = deadline+sample_s
    return sampled_at+sample_s if sampled_at > upcoming else upcoming


def run_recording(destination, plan, spec, image_id, stop_event=None):
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("collection cancelled before simulator launch")
    identity = spec["recording_id"]
    if Path(identity).name != identity or spec["split"] not in ("train", "val", "sealed"):
        raise ValueError("invalid recording identity/split")
    directory = destination / "raw" / spec["split"]
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / f"{identity}.lock"
    token = uuid.uuid4().hex
    with lock.open("x", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "token": token}, stream)
    try:
        return _run_recording(destination, plan, spec, image_id, stop_event)
    finally:
        if json.loads(lock.read_text(encoding="utf-8")).get("token") == token:
            lock.unlink()


def _run_recording(destination, plan, spec, image_id, stop_event=None):
    directory = destination / "raw" / spec["split"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{spec['recording_id']}.jsonl"
    complete = path.with_suffix(".completed.json")
    if complete.exists():
        saved = json.loads(complete.read_text(encoding="utf-8"))
        if saved["spec"] != spec or saved["sha256"] != sha(path) or saved["image_id"] != image_id:
            raise ValueError("completed recording identity changed")
        return saved
    if path.exists():
        raise FileExistsError(f"uncommitted completed path exists: {path}")
    # Each attempt keeps its own partial trace, including failed capability probes.
    partial = directory / f"{spec['recording_id']}.{uuid.uuid4().hex[:10]}.partial.jsonl"
    allocation = start_isolated_container(image_id)
    container_id = allocation["container_id"]
    service = None
    metadata = {"spec": spec, "image_id": image_id, "container_id": container_id,
                "plan_sha256": sha(destination / plan.get("plan_file", "collection-plan.json")),
                "collector_sha256": sha(Path(__file__)), "app_plant_contacted": False,
                "rejected_port_allocations": allocation["rejected_allocations"]}
    try:
        snapshots = destination / "collector-sources"
        snapshots.mkdir(exist_ok=True)
        snapshot = snapshots / f"{metadata['collector_sha256']}.py"
        if not snapshot.exists():
            snapshot.write_bytes(Path(__file__).read_bytes())
        info = json.loads(docker("inspect", container_id))[0]
        assert_owned(info, container_id)
        port = published_port(info)
        metadata["loopback_port"] = port
        metadata["binary_sha256"] = docker("exec", container_id, "sha256sum", "/app/simulation/simulation").split()[0]
        deadline = time.monotonic()+15
        while True:
            try:
                service = PlantService(port=port).start()
                state = service.read()
                break
            except OSError:
                if service is not None:
                    service.stop()
                    service = None
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)
        # Fail before long collection if this image does not implement the
        # documented independent measured/SIS channels or physical percent units.
        raw = service.client.read()
        if not raw.get("measured") or not raw.get("outputs"):
            raise RuntimeError("image lacks native measured/truth sensor interface")
        service.client.write_inputs({f"{v}_valve_sp": value for v, value in plan["calibrated_valves"].items()})
        service.client.write_inputs({"tank_pressure_fault_mode": 4, "tank_pressure_fault_severity": 1.0})
        probe = service.client.read()
        if probe["measured"]["pressure"] != 0 or not 2200 < probe["outputs"]["pressure"] < 3000:
            raise RuntimeError("native sensor fault capability probe failed")
        service.client.write_inputs({"tank_pressure_fault_mode": 0, "tank_pressure_fault_severity": 0})
        metadata["native_dropout_probe_passed"] = True
        service.client.write_inputs({"f1_stuck": 1})
        held_position = service.client.read()["state"]["f1_valve_pos"]
        service.client.write_inputs({"f1_valve_sp": plan["calibrated_valves"]["f1"]+5})
        stuck_probe = service.client.read()["state"]["f1_valve_pos"]
        if abs(stuck_probe-held_position) > .2:
            raise RuntimeError("native stuck-actuator capability probe failed")
        service.client.write_inputs({"f1_stuck": 0, "f1_valve_sp": plan["calibrated_valves"]["f1"]})
        metadata["native_stuck_probe_passed"] = True
        controller = L1ControlStub()
        for component, valve in (("prod_pi", "f1"), ("comp_pi", "f2"), ("pressure_pi", "purge"), ("level_pi", "product")):
            getattr(controller, component).bias = plan["calibrated_valves"][valve]
        started, next_sample, previous = time.monotonic(), 0.0, 0.0
        count, fault_applied, last_progress = 0, False, -30
        with partial.open("x", encoding="utf-8", newline="\n") as stream:
            while (elapsed := time.monotonic()-started) < spec["duration_s"]:
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("collection cancelled; preserving partial trace")
                if elapsed < next_sample:
                    time.sleep(min(.1, next_sample-elapsed))
                    continue
                if not fault_applied and elapsed >= spec["fault_on_s"]:
                    if spec["fault_inputs"]:
                        service.client.write_inputs(spec["fault_inputs"])
                    fault_applied = True
                state = service.read()
                phase = int(elapsed//30) % 3
                targets = SupervisoryTargets(pressure_sp=2700+(spec["pressure_step"] if phase == 1 else 0),
                    level_sp=44.18, production_rate_sp=100+(spec["production_step"] if phase == 2 else 0), a_comp_sp=47)
                command = controller.regulate(targets, state, max(.1, elapsed-previous))
                if spec["planned_class"] == "actuator_fault" and fault_applied:
                    valve = next(k.removesuffix("_stuck") for k in spec["fault_inputs"] if k.endswith("_stuck"))
                    ramp = min(1, (elapsed-spec["fault_on_s"])/10)
                    challenged = plan["calibrated_valves"][valve]+plan["native_stuck_excitation_pp"][valve]*ramp
                    command = replace(command, **{f"{valve}_valve": challenged}).clamped()
                if spec["planned_class"] == "excursion" and fault_applied:
                    if state.pressure < 2820:
                        command = ControlCommand(f1_valve=55, f2_valve=22, purge_valve=0, product_valve=35)
                    else:
                        # Collect sustained near-limit evidence rather than
                        # intentionally driving every remaining sample into trip.
                        command = controller.regulate(SupervisoryTargets(pressure_sp=2860, level_sp=44.18,
                            production_rate_sp=105, a_comp_sp=47), state, max(.1, elapsed-previous))
                if spec.get("driver") == "near_limit_pressure_plateau_v2" and fault_applied:
                    command = pressure_plateau_command(state, spec["pressure_target"])
                state = service.apply(command)
                sampled_at = time.monotonic()-started
                values = {"f1": command.f1_valve, "f2": command.f2_valve,
                          "purge": command.purge_valve, "product": command.product_valve}
                # A protective override is not the requested controller command.
                # Do not invent effective command records for interlocked samples.
                valid_commands = not state.scrammed
                record = {"recording_id": spec["recording_id"], "t": round(sampled_at, 4),
                    "command_requested_at_s": round(elapsed, 4),
                    "measured": measured_dict(state), "commanded": values if valid_commands else None,
                    "requested_commanded": values, "commands_valid": valid_commands,
                    "fault_active": fault_applied, "planned_class": spec["planned_class"],
                    "truth_eval_only": state.truth}
                stream.write(json.dumps(record, allow_nan=False)+"\n")
                count += 1
                if sampled_at-last_progress >= 30:
                    stream.flush()
                    print(json.dumps({"recording": spec["recording_id"], "elapsed_s": round(sampled_at),
                                      "samples": count}), flush=True)
                    last_progress = sampled_at
                previous = elapsed
                next_sample = next_deadline(next_sample, sampled_at, spec["sample_s"])
        partial.replace(path)
        metadata.update(sha256=sha(path), samples=count, complete=True)
        complete.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return metadata
    except BaseException as exc:
        metadata.update(complete=False, error=f"{type(exc).__name__}: {exc}", partial=str(partial))
        partial.with_suffix(".error.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        raise
    finally:
        try:
            if service is not None:
                service.stop()
        finally:
            info = json.loads(docker("inspect", container_id))[0]
            assert_owned(info, container_id)
            docker("rm", "--force", container_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("plan", "preflight", "collect"))
    parser.add_argument("--destination", type=Path, default=RUN)
    parser.add_argument("--only", help="Run just one planned recording for a real smoke")
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 5),
                        help="Up to four independent task-owned simulators; default one")
    parser.add_argument("--supplement", action="store_true", help="Collect the independently allocated excursion supplement")
    args = parser.parse_args()
    plan = ensure_excursion_supplement(args.destination) if args.supplement else ensure_plan(args.destination)
    plan_path = args.destination / plan.get("plan_file", "collection-plan.json")
    print(json.dumps({"collector_sha256": sha(Path(__file__)), "project": str(ROOT),
                      "timing_policy": "timestamp_actual_samples_and_skip_missed_deadlines",
                      "plan_sha256": sha(plan_path)}), flush=True)
    if args.stage == "plan":
        print(json.dumps({"recordings": len(plan["recordings"]), "hours": plan["estimated_sequential_wall_hours"],
                          "plan": str(plan_path)}, indent=2))
        return
    try:
        engine = docker("info", "--format", "{{.ServerVersion}}")
        image_id = docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
    except (OSError, RuntimeError) as exc:
        print(f"Preflight failed: {exc}")
        raise SystemExit(1) from exc
    print(json.dumps({"engine": engine, "image_id": image_id}), flush=True)
    if args.stage == "preflight":
        return
    specs = [s for s in plan["recordings"] if args.only is None or s["recording_id"] == args.only]
    if not specs:
        parser.error("--only does not match a predeclared recording")
    stop_event = threading.Event()
    executor = ThreadPoolExecutor(max_workers=args.workers)
    futures = {executor.submit(run_recording, args.destination, plan, spec, image_id, stop_event): spec for spec in specs}
    try:
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            print(json.dumps({"index": index, "total": len(specs), "recording": futures[future]["recording_id"],
                              "samples": result["samples"], "sha256": result["sha256"]}), flush=True)
    except BaseException:
        stop_event.set()
        for future in futures:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    main()
