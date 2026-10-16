"""
SCRAM live: run the whole stack against the live plant and open the control room.

    python -m live                      # trained model as the brain, demo SIS limits
    python -m live --brain baseline     # rule-based brain instead
    python -m live --production-limits  # 5% low-level trip (level-drift attack then takes ~50 min)
    python -m live --no-reset           # attach to the plant as it is (no container restart)

Starts the GRFICS simulation container if it is not running, checks Ollama, then
serves http://127.0.0.1:8765 (control room, embeds the 3D plant at http://localhost/).
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from l0_plant.safety import TripLimits
from l3_brain.policy import ollama_available
from live.engine import DEMO_LIMITS, Engine
from live.server import serve

REPO = Path(__file__).resolve().parent.parent


def plant_up(host: str = "127.0.0.1", port: int = 55555) -> bool:
    try:
        socket.create_connection((host, port), timeout=1.0).close()
        return True
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m live")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--brain", choices=("ollama", "baseline"), default="ollama")
    ap.add_argument("--production-limits", action="store_true")
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    if not plant_up():
        print("[live] plant not reachable on :55555 - starting the simulation container ...")
        r = subprocess.run(["docker", "compose", "up", "-d", "simulation"], cwd=REPO)
        if r.returncode != 0:
            print("[live] could not start the container. Is Docker Desktop running?")
            return 1
        for _ in range(60):
            if plant_up():
                break
            time.sleep(1)
        else:
            print("[live] plant still not reachable on :55555")
            return 1
    if a.brain == "ollama" and not ollama_available():
        print("[live] Ollama is not serving 'scram-operator' - start Ollama, or the brain falls back to baseline")

    engine = Engine(brain=a.brain, limits=TripLimits() if a.production_limits else DEMO_LIMITS,
                    reset=not a.no_reset).start()
    srv = serve(engine, a.port)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"[live] control room: {url}   (3D plant: http://localhost/)   Ctrl+C to stop")
    if not a.no_browser:
        webbrowser.open(url)
    last = 0
    try:
        import threading
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        while True:
            time.sleep(0.5)
            for e in engine.read(since_event=last)["events"]:
                last = e["id"]
                print(f"  [t={e['t']:7.1f}s] {e['kind']:<10} {e['msg']}")
    except KeyboardInterrupt:
        print("\n[live] stopping")
    finally:
        engine.stop()
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
