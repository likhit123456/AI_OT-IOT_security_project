"""Collect new isolated TE trajectories and seal a measured-telemetry benchmark.

Run on Linux/WSL with a test-only accelerated binary. It starts its own simulator
on an unused port and never connects to the application plant. Synthetic sensor
and actuator-offset cases are identified as such in the manifest.
"""

import argparse
import hashlib
import json
import os
import random
import socket
import subprocess
import time
from pathlib import Path

from l2_monitor import observer_v3_format as F
from training import build_observer_dataset as B

SEED = 99271


class IsolatedPlant:
    def __init__(self, binary):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        self.process = subprocess.Popen(
            [str(binary.resolve())],
            env={**os.environ, "SCRAM_SIM_PORT": str(port)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while True:
            try:
                self.socket = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                if time.monotonic() > deadline:
                    self.close()
                    raise RuntimeError("isolated simulator failed to start")
                time.sleep(0.03)
        self.file = self.socket.makefile("rwb", buffering=0)

    def write(self, valves, faults=None):
        self.file.write(
            (
                json.dumps(
                    {
                        "request": "write",
                        "data": {
                            "inputs": {
                                **{f"{v}_valve_sp": x for v, x in valves.items()},
                                **(faults or {}),
                            }
                        },
                    }
                )
                + "\n"
            ).encode()
        )
        return json.loads(self.file.readline())

    def close(self):
        if hasattr(self, "file"):
            self.file.close()
        if hasattr(self, "socket"):
            self.socket.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def window(plant, rng, scale, excursion=False, profile="steps", faults=None):
    values = {
        k: []
        for k in (
            "pressure",
            "level",
            *F.FLOWS,
            "a_in_purge",
            *(f"{v}_valve_pos" for v in F.VALVES),
            *(f"{v}_valve_cmd" for v in F.VALVES),
            "scrammed",
        )
    }
    start = time.monotonic()
    previous = {"f1": 31.45, "f2": 13.24, "purge": 0.3926, "product": 47.03}
    for i in range(16):
        if excursion:
            valves = {"f1": 100.0, "f2": 100.0, "purge": 0.0, "product": 0.0}
        elif i % 4 == 0:
            target_valves = {
                "f1": rng.uniform(8, 65),
                "f2": rng.uniform(5, 45),
                "purge": rng.uniform(0.2, 12),
                "product": rng.uniform(30, 70),
            }
            if i:
                previous = valves
        if not excursion:
            valves = (
                {
                    v: previous[v] + (target_valves[v] - previous[v]) * (i % 4 + 1) / 4
                    for v in F.VALVES
                }
                if profile == "ramps"
                else target_valves
            )
        target = start + i / scale
        time.sleep(max(0, target - time.monotonic()))
        raw = plant.write(valves, faults)
        measured = raw["outputs"]
        state = raw["state"]
        for key in ("pressure", "level", *F.FLOWS):
            values[key].append(measured["liquid_level" if key == "level" else key])
        values["a_in_purge"].append(measured["A_in_purge"])
        for v in F.VALVES:
            values[f"{v}_valve_pos"].append(state[f"{v}_valve_pos"])
            values[f"{v}_valve_cmd"].append(valves[v])
        values["scrammed"].append(False)
    duration = (time.monotonic() - start) * scale
    measured = [
        {
            k: values[k][i]
            for k in (
                "pressure",
                "level",
                *F.FLOWS,
                "a_in_purge",
                *(f"{v}_valve_pos" for v in F.VALVES),
                "scrammed",
            )
        }
        for i in range(16)
    ]
    cmd = [{v: values[f"{v}_valve_cmd"][i] for v in F.VALVES} for i in range(16)]
    return json.loads(F.telemetry_to_json(measured, cmd, duration))


def collect(binary, destination, scale, seed=SEED, profile="steps", fresh_only=False):
    rng = random.Random(seed)
    rows = []

    def add(label, w, source, kind):
        user = json.dumps(w)
        rows.append(
            {
                "id": hashlib.sha256(user.encode()).hexdigest(),
                "label": label,
                "source": source,
                "kind": kind,
                "window": w,
            }
        )

    normal = []
    for i in range(32):
        plant = IsolatedPlant(binary)
        try:
            w = window(plant, rng, scale, profile=profile)
        finally:
            plant.close()
        if max(w["pressure"]) <= 2790 and min(w["level"]) > 30:
            normal.append(w)
            add("normal", w, f"fresh-normal-{i}", "new simulated trajectory")
        if (i + 1) % 8 == 0:
            print(f"{i + 1}/32 benign trajectories collected", flush=True)
    for i, w in enumerate(normal):
        for mode in ("noise", "drift", "dropout", "frozen"):
            ch = rng.choice(("pressure", "level", *F.FLOWS, "a_in_purge"))
            xs = w[ch]
            if max(xs) <= 0:
                continue
            copied = json.loads(json.dumps(w))
            onset = rng.randint(2, 7)
            amplitude = max(0.01, abs(sum(xs) / 16)) * rng.uniform(0.05, 0.15)
            if mode == "noise":
                ys = [
                    x + (rng.gauss(0, amplitude) if j >= onset else 0)
                    for j, x in enumerate(xs)
                ]
            elif mode == "drift":
                ys = [
                    x
                    + (
                        amplitude * (j - onset) / max(1, 15 - onset)
                        if j >= onset
                        else 0
                    )
                    for j, x in enumerate(xs)
                ]
            elif mode == "dropout":
                ys = [
                    0.0 if j in (onset, onset + 2, onset + 4) else x
                    for j, x in enumerate(xs)
                ]
            else:
                ys = [x if j < onset else xs[onset] for j, x in enumerate(xs)]
            copied[ch] = [
                round(
                    max(0, min(1, x) if ch == "a_in_purge" else x),
                    3 if ch == "a_in_purge" else 2,
                )
                for x in ys
            ]
            if copied[ch] != xs:
                add(
                    "sensor_fault",
                    copied,
                    f"fresh-normal-{i}",
                    f"synthetic {ch}/{mode}",
                )
        copied = json.loads(json.dumps(w))
        valve = rng.choice(F.VALVES)
        copied[f"{valve}_valve_cmd"] = [
            round(min(100, x + rng.uniform(12, 25)), 2) for x in w[f"{valve}_valve_pos"]
        ]
        add(
            "actuator_fault",
            copied,
            f"fresh-normal-{i}",
            f"synthetic {valve}/command-offset",
        )
    for i in range(20):
        plant = IsolatedPlant(binary)
        try:
            w = window(plant, rng, scale, excursion=True)
            if w["pressure"][-1] < 2750:
                w = window(plant, rng, scale, excursion=True)
        finally:
            plant.close()
        if w["pressure"][-1] >= 2750:
            add(
                "excursion",
                w,
                f"fresh-excursion-{i}",
                "new simulated pressure excursion",
            )
    # Native fault recordings were reserved wholesale and were not used in model fitting.
    if fresh_only:
        for valve in F.VALVES:
            for value in (0.55, 0.75):
                plant = IsolatedPlant(binary)
                try:
                    w = window(
                        plant,
                        rng,
                        scale,
                        profile=profile,
                        faults={f"{valve}_cv_scale": value},
                    )
                finally:
                    plant.close()
                add(
                    "actuator_fault",
                    w,
                    f"fresh-capacity/{valve}/{value}",
                    "new simulated capacity fault",
                )
            for _ in range(2):
                plant = IsolatedPlant(binary)
                try:
                    w = window(
                        plant,
                        rng,
                        scale,
                        profile=profile,
                        faults={f"{valve}_stuck": True},
                    )
                finally:
                    plant.close()
                add(
                    "actuator_fault",
                    w,
                    f"fresh-stuck/{valve}",
                    "new simulated stuck actuator",
                )
    native = (
        {}
        if fresh_only
        else {
            name: records
            for name, records in B.load_rollouts(B.OBS_STATES).items()
            if name in B.UNSEEN_NATIVE
        }
    )
    for name, records in sorted(native.items()):
        for chunk in B.windows(records, B.REC):
            if sum(r["fault_active"] for r in chunk) < 20:
                continue
            measured, cmd, seconds = B._win(chunk)
            w = json.loads(F.telemetry_to_json(measured, cmd, seconds))
            add(
                records[0]["kind"],
                w,
                f"reserved-native/{name}",
                "reserved native fault recording",
            )
    rng.shuffle(rows)
    content = "".join(json.dumps(row) + "\n" for row in rows)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8", newline="\n")
    from collections import Counter

    manifest = {
        "seed": seed,
        "profile": profile,
        "time_scale": scale,
        "n": len(rows),
        "by_label": dict(Counter(r["label"] for r in rows)),
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "sources": dict(Counter(r["kind"] for r in rows)),
        "fit_use": False,
        "notes": "Fresh isolated C++ trajectories plus explicit synthetic faults and reserved native recordings; not hardware validation.",
    }
    destination.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--scale", type=float, default=12)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/accuracy-v2/telemetry-holdout.jsonl"),
    )
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--profile", choices=["steps", "ramps"], default="steps")
    parser.add_argument("--fresh-only", action="store_true")
    args = parser.parse_args()
    collect(
        args.binary, args.output, args.scale, args.seed, args.profile, args.fresh_only
    )
