"""Repeatable, non-invasive observer runtime benchmark. Never controls a plant.

Inventory is always read-only. --replay runs measured-input JSONL through the
selected observer; it does not reset a simulator, unload models or alter Ollama.
Input rows: {t, state: PlantState fields, commanded: ControlCommand fields,
intent: SupervisoryTargets fields (optional)}. Labels/truth are never model input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError) as exc:
        return {"unavailable": str(exc)}


def inventory():
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu,power.draw",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        gpu = str(exc)
    try:
        import psutil
        memory = {"system": psutil.virtual_memory()._asdict(),
                  "process_rss_bytes": psutil.Process().memory_info().rss,
                  "cpu_percent": psutil.cpu_percent(),
                  "runners": [{"pid": p.pid, "name": p.info["name"],
                               "rss_bytes": p.memory_info().rss}
                              for p in psutil.process_iter(["name"])
                              if "ollama" in (p.info["name"] or "").lower()]}
    except Exception as exc:  # noqa: BLE001 - optional OS counters may be permission restricted
        memory = {"unavailable": str(exc)}
    return {"python": platform.python_version(), "platform": platform.platform(),
            "cpu": platform.processor(), "logical_cpus": os.cpu_count(), "gpu_csv": gpu,
            "memory": memory, "ollama_version": get_json("http://127.0.0.1:11434/api/version"),
            "installed": get_json("http://127.0.0.1:11434/api/tags"),
            "residency": get_json("http://127.0.0.1:11434/api/ps"),
            "weights": [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size}
                        for p in sorted((ROOT / "model").rglob("*.gguf"))]}


def quantiles(values):
    if not values:
        return {"p50": None, "p95": None, "max": None}
    values = sorted(values)
    return {"p50": statistics.median(values), "p95": values[min(len(values)-1, int(.95*len(values)))],
            "max": max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("legacy", "unified", "none"), default="legacy")
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-every", type=int, default=100)
    args = parser.parse_args()
    if args.sample_every < 1:
        parser.error("sample-every must be positive")
    result = {"schema_version": 1, "mode": args.mode, "seed": 0, "before": inventory(),
              "real_inference": False, "control_jitter_s": None, "energy_joules": None,
              "limitations": ["Replay feed cost is not full plant control-loop timing.",
                              "Total GPU memory includes unrelated workloads.",
                              "RAM/RVRAM samples cannot isolate KV cache from weights.",
                              "Operator contention requires separately running the unchanged operator."]}
    if args.replay:
        import sys
        sys.path.insert(0, str(ROOT))
        from contracts import ControlCommand, PlantState, SupervisoryTargets
        from monitor.observer_runtime import make_observer
        from monitor.telemetry_classifier import approved_specialist
        observer = make_observer(mode=args.mode, telemetry_specialist=approved_specialist())
        durations, samples = [], []
        started = time.perf_counter()
        for index, line in enumerate(args.replay.read_text(encoding="utf-8").splitlines()):
            row = json.loads(line)
            state = dict(row["state"])
            state.pop("truth", None)
            tick = time.perf_counter()
            observer.feed(row["t"], PlantState(**state),
                          SupervisoryTargets(**row["intent"]) if row.get("intent") else None,
                          ControlCommand(**row["commanded"]) if row.get("commanded") else None)
            durations.append(time.perf_counter() - tick)
            if index % args.sample_every == 0:
                samples.append(inventory())
            # Match recorded spacing; do not flood the worker with accelerated ticks.
            if index == 0:
                origin = row["t"]
            time.sleep(max(0, started + row["t"] - origin - time.perf_counter()))
        observer.wait_idle(25)
        result.update({"replay_sha256": hashlib.sha256(args.replay.read_bytes()).hexdigest(),
                       "feed_latency_s": quantiles(durations), "samples": samples,
                       "observer": observer.snapshot(), "elapsed_s": time.perf_counter()-started})
        snapshot = observer.snapshot()
        result["real_inference_attempted"] = snapshot.get("calls", 0) > 0
        result["real_inference"] = snapshot.get("calls", 0) > snapshot.get("failures", 0)
        observer.close()
    result["after"] = inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
