"""
SCRAM live: run the whole stack against the live plant and open the control room.

    python -m live                      # trained model as the brain, demo SIS limits
    python -m live --brain baseline     # rule-based brain instead
    python -m live --production-limits  # 5% low-level trip (level-drift attack then takes ~50 min)
    python -m live --no-reset           # attach to the plant as it is (no container restart)
    python -m live --no-ollama-fix      # leave Ollama's configuration alone

Starts the GRFICS simulation container if it is not running, then serves
http://127.0.0.1:8765 (control room, embeds the 3D plant at http://localhost/).
Persistent Ollama configuration is opt-in through --configure-ollama.
"""

from __future__ import annotations

import argparse
import os
import shutil
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


CACHE_ENV, CACHE_VALUE = "LLAMA_ARG_CACHE_RAM", "0"
OLLAMA_URL = "http://127.0.0.1:11434"


def _ollama_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 11434), timeout=1.0).close()
        return True
    except OSError:
        return False


def _ollama_exe() -> str | None:
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return shutil.which("ollama") or (str(local) if local.is_file() else None)


def _windows_user_env(name: str, value: str | None = None) -> str | None:
    """Read (or, with value, write) a Windows *user* environment variable."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | (winreg.KEY_SET_VALUE if value is not None else 0)) as k:
        if value is not None:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
            import ctypes                             # tell Explorer / new processes
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 2, 5000, None)
            return value
        try:
            return winreg.QueryValueEx(k, name)[0]
        except FileNotFoundError:
            return None


def _start_ollama(exe: str) -> bool:
    env = {**os.environ, CACHE_ENV: CACHE_VALUE}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([exe, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags if sys.platform == "win32" else 0)
    for _ in range(30):
        if _ollama_up():
            return True
        time.sleep(1)
    return False


def ensure_ollama_cache_cap() -> None:
    """The live stack keeps three models resident (operator, observer v2, observer v3).
    Ollama's bundled llama-server otherwise keeps a host-RAM prompt cache of up to
    8 GB PER MODEL, which exhausted a 16 GB laptop in ~4.5 min (model/README.md).
    Make sure the Ollama server runs with LLAMA_ARG_CACHE_RAM=0: persist it as a user
    variable on Windows, and (re)start the server with it if needed."""
    exe = _ollama_exe()
    if exe is None:
        print("[live] Ollama not found - the brain falls back to baseline, no observer models")
        return
    needs_restart = False
    if sys.platform == "win32":
        if _windows_user_env(CACHE_ENV) != CACHE_VALUE:
            _windows_user_env(CACHE_ENV, CACHE_VALUE)
            print(f"[live] set user environment variable {CACHE_ENV}={CACHE_VALUE} (caps Ollama's prompt cache)")
            needs_restart = _ollama_up()           # a running server started without it
    elif os.environ.get(CACHE_ENV) != CACHE_VALUE and _ollama_up():
        print(f"[live] WARNING: if this Ollama server was not started with {CACHE_ENV}={CACHE_VALUE}, "
              "three resident models can exhaust RAM; restart it as: "
              f"{CACHE_ENV}={CACHE_VALUE} ollama serve")
    if needs_restart:
        print("[live] restarting Ollama so its model runners pick the cap up ...")
        if sys.platform == "win32":
            for name in ("ollama app.exe", "ollama.exe", "llama-server.exe"):
                subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)
            time.sleep(2)
    if not _ollama_up():
        print("[live] starting Ollama with the prompt-cache cap ...")
        if not _start_ollama(exe):
            print("[live] Ollama did not come up - the brain falls back to baseline")


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m live")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--brain", choices=("ollama", "baseline"), default="ollama")
    ap.add_argument("--production-limits", action="store_true")
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-ollama-fix", action="store_true", help="compatibility flag: configuration is now opt-in")
    ap.add_argument("--configure-ollama", action="store_true", help="explicitly set the cache cap and restart Ollama if necessary")
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
    if a.configure_ollama and not a.no_ollama_fix:
        ensure_ollama_cache_cap()
    if a.brain == "ollama" and not ollama_available():
        print("[live] Ollama is not serving 'scram-operator' - start Ollama, or the brain falls back to baseline")

    engine = Engine(brain=a.brain, limits=TripLimits() if a.production_limits else DEMO_LIMITS,
                    reset=not a.no_reset)
    try:
        srv = serve(engine, a.port)
    except OSError as exc:
        print(f"[live] could not bind the control room to port {a.port}: {exc}", file=sys.stderr)
        return 1
    engine.start()
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
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
