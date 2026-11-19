"""Task-owned ordinary Ollama server for the opt-in demo; no global settings."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import psutil

from training.shared_slm_data import ROOT, sha
from training.train_demo_observer import DESTINATION

URL = "http://127.0.0.1:11438"
ALIAS = "scram-observer-demo"
EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"


def owned_process(record):
    try:
        process = psutil.Process(record["pid"])
        if (abs(process.create_time()-record["create_time"]) > .01
                or process.cmdline() != record["command"]):
            raise RuntimeError("Recorded PID is not the task-owned Ollama process")
        return process
    except psutil.NoSuchProcess:
        return None


def start(run=DESTINATION):
    folder = run / "ollama-service"
    folder.mkdir(exist_ok=True)
    record_path = folder / "process.json"
    if record_path.exists():
        record = json.loads(record_path.read_text())
        if owned_process(record):
            return record
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 11438))  # Refuse to reuse an unrelated listener.
    if not EXE.is_file():
        raise FileNotFoundError(EXE)
    command = [str(EXE), "serve"]
    attempt = time.time_ns()
    environment = {**os.environ, "OLLAMA_HOST": URL.removeprefix("http://"),
                   "OLLAMA_MODELS": str(run / "ollama-models"),
                   "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1"}
    with (folder / f"stdout-{attempt}.log").open("xb") as out, (folder / f"stderr-{attempt}.log").open("xb") as err:
        child = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=out, stderr=err,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    record = {"pid": child.pid, "create_time": psutil.Process(child.pid).create_time(),
              "command": command, "url": URL, "binary_sha256": sha(EXE),
              "model_store": environment["OLLAMA_MODELS"], "task_owned": True}
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    try:
        deadline = time.monotonic()+45
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"Demo Ollama exited {child.returncode}; see {folder}")
            try:
                with urllib.request.urlopen(URL+"/api/version", timeout=1) as response:
                    if response.status == 200:
                        return record
            except OSError:
                time.sleep(.25)
        raise TimeoutError("Demo Ollama did not become ready")
    except BaseException:
        stop(run)
        raise


def stop(run=DESTINATION):
    path = run / "ollama-service/process.json"
    if path.exists():
        process = owned_process(json.loads(path.read_text()))
        if process:
            process.terminate()
            process.wait(20)


if __name__ == "__main__":
    print(json.dumps(start()))
