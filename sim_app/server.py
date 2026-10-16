"""
TE reactor operator sim: a clean manual-control front end for the live GRFICS
plant (:55555). No interlock, no provenance gate, no monitor, no attacker - the
operator's valve moves go straight to the simulator.

The sim's real manipulated inputs (TE_process.cc / the running image):
    f1_valve_sp, f2_valve_sp, purge_valve_sp, product_valve_sp  (0-100 %)
    e_stop                                                      (0/1)
There is NO coolant / cooling input and no temperature state in this TE model;
over-pressure is its failure mode.

Rupture: GRFICS represents the explosion in its Unity 3D client, not the
simulator. TenEastSimulationConnector polls pressure and, once it crosses
`explosion_threshold`, latches `exploded` and plays ExplosionManager /
PostExplosionEffects (vessel blown open, steam, camera shake, cracked glass).
Measured on this image: intact at 3087 kPa, ruptured by 3122 kPa (threshold
3100); it stays ruptured when pressure falls again and only a simulation reset
clears it. The simulator itself clamps pressure at 3200 kPa. This server
mirrors that latch so the page can show it.

AI operator (sim_app/ai.py): plain-English commands parsed by the stock
qwen2.5:1.5b, and a "save the reactor" closed loop flown by the trained
scram-operator. Both go through the same valve path as the manual controls.

Run:  python -m sim_app            (http://127.0.0.1:8770)
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
import time
from collections import deque
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from l0_plant.grfics_client import GrficsClient
from scripts.reset_plant import reset_and_settle
from sim_app.ai import Activity, Rescue, parse_commands

RUPTURE_THRESHOLD_KPA = 3100.0     # GRFICS 3D client's explosion_threshold (measured)
PRESSURE_CEILING_KPA = 3200.0      # simulator clamp (TE_process.cc)
POLL_S = 0.5
HISTORY_N = 2400                   # 20 min at 2 Hz
VALVES = ("f1", "f2", "purge", "product")
STATIC = Path(__file__).resolve().parent / "static"


class Plant:
    """Owns the simulator connection, the trend history and the rupture latch."""

    def __init__(self, host: str = "127.0.0.1", port: int = 55555):
        self.host, self.port = host, port
        self._io = threading.Lock()          # one request at a time on the socket
        self._data = threading.Lock()
        self._client: Optional[GrficsClient] = None
        self.raw: dict = {}
        self.connected = False
        self.resetting = False
        self._reset_lock = threading.Lock()   # one reset at a time, whoever asks
        self.setpoints: dict = {}
        self.history: deque = deque(maxlen=HISTORY_N)
        self.seq = 0
        self.t0 = time.monotonic()
        self._clear_rupture()
        self.activity = Activity()
        self.rescue = Rescue(self, self.activity)

    # ---- socket -------------------------------------------------------------
    def _txn(self, fn):
        with self._io:
            try:
                if self._client is None:
                    self._client = GrficsClient(self.host, self.port, timeout=3.0).connect()
                return fn(self._client)
            except (OSError, ValueError):
                if self._client is not None:
                    self._client.close()
                self._client = None
                raise

    def read(self) -> dict:
        return self._txn(lambda c: c.read())

    def write(self, inputs: dict) -> dict:
        return self._txn(lambda c: c.write_inputs(inputs))

    # ---- polling ------------------------------------------------------------
    def _clear_rupture(self) -> None:
        self.ruptured, self.ruptured_t, self.rupture_pressure, self.peak_pressure = False, None, None, 0.0

    def start(self) -> "Plant":
        threading.Thread(target=self._poll, name="plant-poll", daemon=True).start()
        return self

    def _poll(self) -> None:
        while True:
            if not self.resetting:
                try:
                    raw = self.read()
                    self._ingest(raw)
                except (OSError, ValueError):
                    self.connected = False
            time.sleep(POLL_S)

    def _ingest(self, raw: dict) -> None:
        o, s = raw.get("outputs", {}), raw.get("state", {})
        t = round(time.monotonic() - self.t0, 1)
        p = float(o.get("pressure", 0.0))
        with self._data:
            self.raw, self.connected = raw, True
            # Valves follow their setpoint instantly in this sim, except that e_stop forces
            # 0/0/100/100 and those positions persist after release. Mirroring the plant keeps
            # the +/- steps based on where each valve really is, however it got there.
            if s:
                self.setpoints = {v: float(s.get(f"{v}_valve_pos", 0.0)) for v in VALVES}
            self.peak_pressure = max(self.peak_pressure, p)
            if not self.ruptured and p >= RUPTURE_THRESHOLD_KPA:
                self.ruptured, self.ruptured_t, self.rupture_pressure = True, t, round(p, 1)
            self.seq += 1
            self.history.append([self.seq, t, round(p, 2), round(o.get("liquid_level", 0.0), 3),
                                 *(round(o.get(f"{v}_flow", 0.0), 2) for v in VALVES),
                                 *(round(s.get(f"{v}_valve_pos", 0.0), 2) for v in VALVES)])

    # ---- views / actions ----------------------------------------------------
    def snapshot(self, since: int = 0) -> dict:
        with self._data:
            o, s = self.raw.get("outputs", {}), self.raw.get("state", {})
            return {
                "connected": self.connected, "resetting": self.resetting,
                "t": round(time.monotonic() - self.t0, 1),
                "pressure": o.get("pressure"), "level": o.get("liquid_level"),
                "flows": {v: o.get(f"{v}_flow") for v in VALVES},
                "composition_purge": {"A": o.get("A_in_purge"), "B": o.get("B_in_purge"),
                                      "C": o.get("C_in_purge")},
                "cost": o.get("cost"),
                "valves": {v: s.get(f"{v}_valve_pos") for v in VALVES},
                "setpoints": dict(self.setpoints),
                "e_stop": bool(s.get("e_stop", False)),
                "ruptured": self.ruptured, "exploded": self.ruptured,
                "ruptured_t": self.ruptured_t, "rupture_pressure": self.rupture_pressure,
                "peak_pressure": round(self.peak_pressure, 1),
                "rupture_threshold": RUPTURE_THRESHOLD_KPA, "pressure_ceiling": PRESSURE_CEILING_KPA,
                "history": [h for h in self.history if h[0] > since],
            }

    def _check_writable(self, source: str, valves: bool = True) -> None:
        if self.resetting:
            raise HTTPException(409, "simulation is resetting")
        if self.ruptured:
            raise HTTPException(409, "the reactor has ruptured; reset the simulation")
        if source == "manual" and self.rescue.active:
            raise HTTPException(409, "the AI is in control; press 'Stop AI' to take manual control")
        if valves and source == "manual" and self.raw.get("state", {}).get("e_stop"):
            # the simulator ignores valve setpoints while e_stop is on
            raise HTTPException(409, "E-STOP is engaged; release it before moving valves")

    def apply_valves(self, valves: dict, source: str = "manual") -> None:
        """Write several valve setpoints at once (clamped to 0-100)."""
        self._check_writable(source)
        new = {v: max(0.0, min(100.0, float(x))) for v, x in valves.items() if v in VALVES}
        raw = self.write({f"{v}_valve_sp": x for v, x in new.items()})
        with self._data:
            self.setpoints.update(new)
        self._ingest(raw)

    def manipulate(self, variable: str, delta: Optional[float], value: Optional[float],
                   source: str = "manual") -> dict:
        self._check_writable(source, valves=variable != "e_stop")
        if variable == "e_stop":
            on = bool(value) if value is not None else not bool(self.raw.get("state", {}).get("e_stop"))
            try:
                raw = self.write({"e_stop": int(on)})
            except (OSError, ValueError) as e:
                raise HTTPException(503, f"plant not reachable: {e}") from e
            self._ingest(raw)
            return {"variable": "e_stop", "value": on}
        with self._data:
            base = self.setpoints.get(variable, 0.0)
        new = max(0.0, min(100.0, float(value) if value is not None else base + float(delta)))
        try:
            raw = self.write({f"{variable}_valve_sp": new})
        except (OSError, ValueError) as e:
            raise HTTPException(503, f"plant not reachable: {e}") from e
        with self._data:
            self.setpoints[variable] = new
        self._ingest(raw)
        return {"variable": variable, "setpoint": new}

    def reset(self) -> dict:
        if not self._reset_lock.acquire(blocking=False):
            raise HTTPException(409, "already resetting")
        self.rescue.stop("simulation reset")
        self.resetting = True
        try:
            with self._io:
                if self._client is not None:
                    self._client.close()
                self._client = None
            point = reset_and_settle()          # docker compose restart + wait settled
            with self._data:
                self._clear_rupture()
                self.history.clear()
                self.setpoints = {}
                self.t0 = time.monotonic()
            self._ingest(self.read())
            return {"pressure": point.pressure, "level": point.level,
                    "cycles_to_settle": point.cycles_to_settle}
        except Exception as e:                  # noqa: BLE001
            raise HTTPException(500, f"reset failed: {type(e).__name__}: {e}") from e
        finally:
            self.resetting = False
            self._reset_lock.release()


class Manipulation(BaseModel):
    variable: Literal["f1", "f2", "purge", "product", "e_stop"]
    delta: Optional[float] = None
    value: Optional[float] = None

    @model_validator(mode="after")
    def one_of(self):
        if self.variable != "e_stop" and (self.delta is None) == (self.value is None):
            raise ValueError("give exactly one of 'delta' or 'value'")
        return self


plant = Plant()


@asynccontextmanager
async def lifespan(_app):
    plant.start()
    yield


app = FastAPI(title="TE reactor operator sim", lifespan=lifespan)


@app.get("/api/state")
def get_state(since: int = 0, since_ai: int = 0) -> dict:
    return {**plant.snapshot(since),
            "ai": {"active": plant.rescue.active, "activity": plant.activity.since(since_ai)}}


@app.post("/api/manipulate")
def manipulate(m: Manipulation) -> dict:
    return plant.manipulate(m.variable, m.delta, m.value)


class Command(BaseModel):
    text: str


@app.post("/api/command")
def command(c: Command) -> dict:
    """Plain English -> one or more actions (stock qwen2.5:1.5b), applied in the order given,
    each exactly like a manual move. Each action's outcome is reported on its own; one
    refused action does not stop the others."""
    text = c.text.strip()[:300]
    if not text:
        raise HTTPException(400, "empty command")
    try:
        parsed = parse_commands(text)
    except Exception as e:                      # noqa: BLE001
        plant.activity.add("error", f'"{text}": language model unavailable ({type(e).__name__})', command=text)
        raise HTTPException(503, f"language model unavailable: {e}") from e
    steps = []
    for a in parsed["actions"]:
        act, valve, amount = a["action"], a["valve"], a["amount"]
        step = {k: a[k] for k in ("clause", "action", "valve", "amount", "corrections")}
        try:
            if act == "rescue":
                step["result"], step["message"] = rescue(), "AI rescue engaged"
            elif act == "stop":
                step["result"], step["message"] = stop_ai(), "AI stopped; manual control"
            elif act in ("adjust", "set"):
                r = plant.manipulate(valve, amount if act == "adjust" else None, amount if act == "set" else None)
                step["result"], step["message"] = r, f"{valve} setpoint -> {r['setpoint']:.1f}%"
            else:
                step["error"] = "not understood as a valve command"
        except HTTPException as e:
            step["error"] = str(e.detail)
        step["ok"] = "error" not in step
        steps.append(step)
    summary = "; ".join(st["message"] if st["ok"] else f'"{st["clause"] or text}": {st["error"]}' for st in steps)
    info = {"command": text, "actions": steps, "model_output": parsed["model_output"],
            "latency_s": parsed["latency_s"]}
    ok = [st for st in steps if st["ok"]]
    plant.activity.add("command" if ok else "error", summary, **info)
    if not ok:
        if all(st["action"] == "unknown" for st in steps):
            raise HTTPException(422, "not understood as a valve command")
        raise HTTPException(409, summary)
    first = steps[0]
    return {**info, "message": summary,
            # single-action fields, kept for existing clients
            "parsed": {k: first[k] for k in ("action", "valve", "amount")},
            "corrections": [c for st in steps for c in st["corrections"]],
            "result": first.get("result")}


@app.post("/api/ai/rescue")
def rescue() -> dict:
    if plant.ruptured:
        raise HTTPException(409, "the reactor has ruptured; reset the simulation")
    if plant.resetting:
        raise HTTPException(409, "simulation is resetting")
    plant.rescue.start()
    return {"active": True}


@app.post("/api/ai/stop")
def stop_ai() -> dict:
    plant.rescue.stop()
    return {"active": False}


@app.post("/api/reset")
def reset() -> dict:                            # sync: runs in FastAPI's threadpool
    return plant.reset()


NO_CACHE = {"Cache-Control": "no-cache"}       # browsers revalidate, so frontend edits show on refresh


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers=NO_CACHE)


class _FrontendFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE)
        return response


app.mount("/static", _FrontendFiles(directory=STATIC), name="static")
