"""python -m sim_app [--port 8770] [--no-browser]  -  starts the plant container if needed."""

from __future__ import annotations

import argparse
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

REPO = Path(__file__).resolve().parent.parent


def plant_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 55555), timeout=1.0).close()
        return True
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m sim_app")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if not plant_up():
        print("[sim_app] plant not reachable on :55555 - starting the simulation container ...")
        if subprocess.run(["docker", "compose", "up", "-d", "simulation"], cwd=REPO).returncode != 0:
            print("[sim_app] could not start the container. Is Docker Desktop running?")
            return 1
        for _ in range(60):
            if plant_up():
                break
            time.sleep(1)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"[sim_app] {url}   (3D plant: http://localhost/)   Ctrl+C to stop", flush=True)
    if not a.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run("sim_app.server:app", host="127.0.0.1", port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
