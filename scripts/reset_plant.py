"""
Reset the plant container to its designed operating point and confirm it has
settled before a run depends on it.

Why this exists: PlantService doesn't persist any state of its own, but the
simulator container does, in the running TE process - whatever valve
positions, e_stop, and sensor faults a previous run or test left behind carry
over into the next one. A run that assumes it's starting near the ~2700 kPa /
~44% design point can instead inherit a mid-transient state (e.g. valves
pinned wide open from an aborted trip test), which silently turns a
"baseline holds in band" demo into a recovery-transient demo, or makes an
interlock trip test time out because truth is starting far from the limit.

What `docker compose restart simulation` does: it restarts the container's
PID 1 (supervisord), which respawns the simulation binary. The TE object
lives only in that process's memory (no volume persists it), so a fresh
process means a fresh TE() constructor - which calls steady_state() and
lands pressure at 2700 kPa, liquid_level at ~44.2%, and all four valves at
their physics-derived steady positions. Confirmed empirically: after a
restart, truth drifts <1 kPa over the first 12 reads with no valve commands
sent - it is already essentially settled, not just pointed at a target. This
script still polls for stability rather than assuming that, so it also works
if a future image version needs a real warmup.

Usage:
    python -m scripts.reset_plant                 # restart + wait, print the settled point
    python -m scripts.reset_plant --no-restart     # skip the restart, just wait/confirm settled

As a library:
    from scripts.reset_plant import reset_and_settle
    point = reset_and_settle()   # dict: pressure, level, valve positions, cycles_to_settle
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST, PORT = "127.0.0.1", 55555

# GRFICS bug workaround. TE::steady_state() (simulation/simulation/TE_process.cc) solves
# the three design flows into b[0..2] but sets the purge valve from b[3], one past the
# end of the array, so a fresh simulator starts with the purge at memory garbage (~0)
# instead of its design opening. Feed 1 carries 0.5% inert B whose only exit is the
# purge, so with the purge shut B piles up and pressure creeps up ~3 kPa/min at the
# "design point", faster the more feed 1 is open. The value below is what the
# simulator's own steady-state equations give for the purge valve (purge flow
# 7.05 kmol/h at 2700 kPa); measured live it holds pressure flat (-0.2 kPa/min).
DESIGN_PURGE_VALVE_PCT = 0.3926


@dataclass
class SettledPoint:
    pressure: float
    level: float
    product_flow: float
    f1_valve_pos: float
    f2_valve_pos: float
    purge_valve_pos: float
    product_valve_pos: float
    cycles_to_settle: int
    pressure_range: float
    level_range: float


def restart_container(service: str = "simulation", timeout_s: float = 60.0) -> None:
    """`docker compose restart <service>`. Raises on failure."""
    subprocess.run(
        ["docker", "compose", "restart", service],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True, timeout=timeout_s,
    )


def wait_reachable(host: str = HOST, port: int = PORT, timeout_s: float = 60.0) -> None:
    """Block until the simulator accepts a TCP connection, or raise TimeoutError."""
    deadline = time.monotonic() + timeout_s
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError as e:
            last_err = e
            time.sleep(1)
    raise TimeoutError(f"plant not reachable on {host}:{port} within {timeout_s}s") from last_err


def wait_settled(
    host: str = HOST, port: int = PORT,
    stable_cycles: int = 10, poll_s: float = 1.0,
    pressure_tol_kpa: float = 2.0, level_tol_pct: float = 0.1,
    max_wait_s: float = 120.0,
) -> SettledPoint:
    """
    Poll read-only (sends no writes, so it doesn't disturb whatever it's
    measuring) until pressure and level have both stayed within tolerance
    for `stable_cycles` consecutive reads, or raise TimeoutError.
    """
    import json

    def read_once() -> Optional[dict]:
        """One request on a fresh connection; None on any transient failure
        (reconnecting after a restart can race the container coming up)."""
        try:
            with socket.create_connection((host, port), timeout=5) as s:
                f = s.makefile("rwb")
                f.write(b'{"request":"read"}\n')
                f.flush()
                line = f.readline()
                if not line:
                    return None
                return json.loads(line)
        except (OSError, json.JSONDecodeError):
            return None

    pressures: list[float] = []
    levels: list[float] = []
    deadline = time.monotonic() + max_wait_s
    last_raw = None
    while time.monotonic() < deadline:
        raw = read_once()
        if raw is None:
            time.sleep(poll_s)
            continue
        last_raw = raw
        o = last_raw["outputs"]
        pressures.append(o["pressure"])
        levels.append(o["liquid_level"])
        if len(pressures) >= stable_cycles:
            window_p = pressures[-stable_cycles:]
            window_l = levels[-stable_cycles:]
            p_range = max(window_p) - min(window_p)
            l_range = max(window_l) - min(window_l)
            if p_range <= pressure_tol_kpa and l_range <= level_tol_pct:
                s_ = last_raw["state"]
                o_ = last_raw["outputs"]
                return SettledPoint(
                    pressure=o_["pressure"], level=o_["liquid_level"],
                    product_flow=o_["product_flow"],
                    f1_valve_pos=s_["f1_valve_pos"], f2_valve_pos=s_["f2_valve_pos"],
                    purge_valve_pos=s_["purge_valve_pos"], product_valve_pos=s_["product_valve_pos"],
                    cycles_to_settle=len(pressures),
                    pressure_range=p_range, level_range=l_range,
                )
        time.sleep(poll_s)

    detail = ""
    if last_raw is not None:
        o = last_raw["outputs"]
        detail = f" (last reading: P={o['pressure']:.1f} kPa, L={o['liquid_level']:.2f}%)"
    raise TimeoutError(
        f"plant did not settle within {pressure_tol_kpa} kPa / {level_tol_pct}% "
        f"over {stable_cycles} cycles within {max_wait_s}s{detail}"
    )


def set_design_purge(host: str = HOST, port: int = PORT, timeout_s: float = 60.0) -> None:
    """Write the corrected design purge opening (see DESIGN_PURGE_VALVE_PCT) and confirm
    the simulator reports it. Retries: right after a restart Docker's port proxy accepts
    connections before the simulator inside is listening, and drops what is sent."""
    import json
    msg = (json.dumps({"request": "write", "data": {"inputs": {
        "purge_valve_sp": DESIGN_PURGE_VALVE_PCT}}}) + "\n").encode()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5) as s:
                f = s.makefile("rwb")
                f.write(msg)
                f.flush()
                line = f.readline()
            if line:
                pos = json.loads(line).get("state", {}).get("purge_valve_pos")
                if pos is not None and abs(pos - DESIGN_PURGE_VALVE_PCT) < 1e-3:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"could not set the design purge ({DESIGN_PURGE_VALVE_PCT}%) within {timeout_s}s")


def reset_and_settle(restart: bool = True, fix_purge: bool = True, **settle_kwargs) -> SettledPoint:
    """Restart the simulator, apply the corrected design purge, wait until settled."""
    if restart:
        restart_container()
        wait_reachable()
    if fix_purge:
        set_design_purge()
    return wait_settled(**settle_kwargs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-restart", action="store_true",
                     help="skip docker compose restart; just wait for / confirm settled")
    args = ap.parse_args()

    try:
        point = reset_and_settle(restart=not args.no_restart)
    except (TimeoutError, subprocess.CalledProcessError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    print(f"settled after {point.cycles_to_settle} cycles "
          f"(range: {point.pressure_range:.2f} kPa, {point.level_range:.3f} %)")
    print(f"  pressure       = {point.pressure:.1f} kPa")
    print(f"  level          = {point.level:.2f} %")
    print(f"  product_flow   = {point.product_flow:.2f} kmol/h")
    print(f"  f1_valve_pos   = {point.f1_valve_pos:.2f} %")
    print(f"  f2_valve_pos   = {point.f2_valve_pos:.2f} %")
    print(f"  purge_valve_pos= {point.purge_valve_pos:.2f} %")
    print(f"  product_valve_pos={point.product_valve_pos:.2f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
