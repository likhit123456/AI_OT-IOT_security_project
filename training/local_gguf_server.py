"""Manage only this experiment's recorded llama.cpp process and alias registry."""
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
from training.shared_slm_native import write

URL = "http://127.0.0.1:11437"
BINARY = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/lib/ollama/llama-server.exe"
RUNTIME = "Ollama-bundled llama.cpp / CUDA v12; exact binary identity recorded"


def registry(run):
    path = run / "llama-server/registry.json"
    return json.loads(path.read_text()) if path.exists() else {}


def register(run, alias, model, context):
    values = registry(run)
    values[alias] = {"model": str(model.resolve()), "sha256": sha(model), "context": context}
    write(run / "llama-server/registry.json", values)


def ensure(run, alias):
    entry = registry(run)[alias]
    record = run / "llama-server/process.json"
    previous = json.loads(record.read_text()) if record.exists() else {}
    pid = previous.get("pid")
    if pid and psutil.pid_exists(pid):
        process = psutil.Process(pid)
        actual = process.cmdline()
        if (actual != previous.get("command") or not actual or Path(actual[0]).name != "llama-server.exe"
                or ("create_time" in previous and abs(process.create_time()-previous["create_time"]) > .01)):
            raise RuntimeError("recorded PID does not belong to the task server")
        if (previous.get("sha256") == entry["sha256"] and previous.get("alias") == alias
                and previous.get("runtime") == RUNTIME and previous.get("context") == entry["context"]):
            return previous
        process.terminate()
        process.wait(20)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 11437))  # Never adopt an unrelated healthy listener.
    command = [str(BINARY), "-m", entry["model"],
               "--host", "127.0.0.1", "--port", "11437", "-c", str(entry["context"]),
               "-ngl", "99", "--device", "CUDA0", "-lv", "4", "-t", "8", "-tb", "8", "--parallel", "1", "--jinja",
               "--alias", alias, "--cache-ram", "0"]
    folder = record.parent
    folder.mkdir(parents=True, exist_ok=True)
    # Inspect the executable before launching so a version-query failure cannot
    # leave an unrecorded inference process behind.
    version = subprocess.run([str(BINARY), "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=10, check=False)
    with (folder / (alias+"-stdout.log")).open("ab") as stdout, (folder / (alias+"-stderr.log")).open("ab") as stderr:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                    env={**os.environ, "PATH": str(BINARY.parent / "cuda_v12")+os.pathsep+str(BINARY.parent)+os.pathsep+os.environ["PATH"],
                         "GGML_BACKEND_PATH": str(BINARY.parent / "cuda_v12/ggml-cuda.dll")},
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        info = {**entry, "alias": alias, "pid": process.pid, "command": command,
                "create_time": psutil.Process(process.pid).create_time(),
                "runtime": RUNTIME, "url": URL, "task_owned": True,
                "binary_version": version.stdout+version.stderr,
                "binary_sha256": sha(BINARY), "cuda_backend_sha256": sha(BINARY.parent / "cuda_v12/ggml-cuda.dll")}
        write(record, info)
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"llama.cpp server exited: {process.returncode}; see {folder}")
            try:
                with urllib.request.urlopen(URL+"/health", timeout=2) as response:
                    if json.load(response).get("status") == "ok":
                        return info
            except OSError:
                time.sleep(.5)
        raise TimeoutError("local GGUF server did not become healthy")
    except BaseException:
        if process.poll() is None:
            process.terminate()
            process.wait(20)
        raise


def residency(run):
    info = json.loads((run / "llama-server/process.json").read_text())
    process = psutil.Process(info["pid"])
    with urllib.request.urlopen(URL+"/v1/models", timeout=5) as response:
        models = json.load(response)
    return {"process": info, "rss_bytes": process.memory_info().rss, "models": models,
            "real_model_hash_verified": sha(Path(info["model"])) == info["sha256"]}
