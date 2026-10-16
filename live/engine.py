"""
SCRAM live engine: the whole stack running continuously against the live
GRFICS plant, with the trained operator model as the brain.

One control thread owns the plant connection and runs, every cycle:

    L3  AIOperator(OllamaOperator 'scram-operator' | BaselineOperator)   intent
          | governance authority (0..1, set from the control room)
    L2  BehaviorMonitor (measured only) - flags, never acts
    L1  LiveL1 (the stub, feedforward production), bumpless-primed, signs every command
    L0  PlantService(gate=ProvenanceGate, limits=TripLimits) -> plant
          SIS interlock on the independent safety channel

Scenarios (attacks / faults) are queued from the HTTP thread and executed on
the control thread, so nothing else ever touches the plant socket - except the
"out-of-band write" scenario, which deliberately opens its own connection to
:55555 the way an attacker on the plant network would.
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Callable, Deque, Optional

from contracts import ControlCommand, PlantState, SupervisoryTargets
from l0_plant import fault_injection as fi
from l0_plant.grfics_client import GrficsClient
from l0_plant.plant_service import PlantService
from l0_plant.provenance_gate import CommandSigner, ProvenanceGate
from l0_plant.safety import TripLimits
from l1_stub import L1ControlStub
from l3_brain.operator import AIOperator
from l3_brain.policy import (BaselineOperator, LLMOperator, OllamaOperator,
                             ollama_available)
from monitor.behavior_monitor import BehaviorMonitor, MonitorLimits
from scripts.reset_plant import DESIGN_PURGE_VALVE_PCT, reset_and_settle

L1_KEY = b"scram-live-l1-key-not-a-real-secret"
ATTACKER_KEY = b"attacker-guess"
POLL_S = 0.05                 # same cadence as the validated experiments
DT = 0.5                      # L1 integration step (as in every experiment)
HISTORY_S = 0.5               # trend sample period
HISTORY_N = 3600              # 30 min of trend at 2 Hz
# Production SIS limits, except the low-level trip, which is raised to 40%
# (re-arm 42%) so the level-drift attack trips in ~6 min instead of ~50:
# the drain is physics-capped at ~0.013 %/s (see experiments/level_drift_eval.py).
DEMO_LIMITS = TripLimits(level_low=40.0, level_low_rearm=42.0)
DRIFT_RATE_PCT_PER_HR = 60.0
# Valve lineup of the settled design point (training/design_point.json). After a
# trip is re-armed the plant restarts from this lineup: the e-stop leaves feeds
# shut and product wide open, a state the L1 stub's loops cannot climb out of
# (high product flow reads as "over production" and keeps feed 1 shut).
DESIGN_VALVES = json.loads((Path(__file__).resolve().parent.parent / "training" / "design_point.json")
                           .read_text())["valves"]
# training/design_point.json records the purge as the simulator reports it (~0, a GRFICS
# bug); restart from the corrected design opening (scripts/reset_plant.py).
DESIGN_VALVES = {**DESIGN_VALVES, "purge": DESIGN_PURGE_VALVE_PCT}


DESIGN_PRODUCTION = 100.0


class LiveL1(L1ControlStub):
    """The L1 stub with its production loop replaced by feedforward.

    The stub regulates production by moving feed 1 on measured product flow,
    but product flow is set mostly by the product valve, which the level loop
    owns. Away from the design point the two loops fight and feed 1 winds shut
    (the plant never recovers from an e-stop). Here feed 1 is scheduled from
    the production setpoint instead: f1 = design opening x (sp / 100). The
    pressure (purge), level (product) and composition (feed 2) PI loops are
    the stub's own. The teammate's real L1 replaces all of this."""

    def regulate(self, targets: SupervisoryTargets, state: PlantState, dt: float) -> ControlCommand:
        cmd = super().regulate(targets, state, dt)
        if targets.production_rate_sp is None:
            return cmd
        f1 = DESIGN_VALVES["f1"] * targets.production_rate_sp / DESIGN_PRODUCTION
        return replace(cmd, f1_valve=max(0.0, min(100.0, f1)))


def prime_l1(l1: L1ControlStub, state: PlantState) -> None:
    """Bumpless transfer: start every PI loop from the valve position the plant
    already holds, so taking control does not kick the valves (the stub's fixed
    biases would open purge to 40% from 0% and pressure would sag)."""
    for pi, pos in ((l1.pressure_pi, state.purge_valve_pos), (l1.level_pi, state.product_valve_pos),
                    (l1.prod_pi, state.f1_valve_pos), (l1.comp_pi, state.f2_valve_pos)):
        pi.bias, pi.i = float(pos), 0.0


def _num(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), 3)


class Engine:
    def __init__(self, brain: str = "ollama", limits: TripLimits = DEMO_LIMITS,
                 reset: bool = True, host: str = "127.0.0.1", port: int = 55555):
        self.host, self.port, self.limits = host, port, limits
        self.want_reset = reset
        self.brain_kind = brain
        self.authority = 1.0
        self._q: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.status = "starting"
        self.error = ""
        self.t0 = time.monotonic()
        self.cycles = 0
        self.events: Deque[dict] = deque(maxlen=300)
        self._ev_seq = 0
        self.history: Deque[list] = deque(maxlen=HISTORY_N)
        self._hist_seq = 0
        self.decisions: Deque[dict] = deque(maxlen=200)
        self.scenarios: dict = {}           # name -> {"until": t, ...}
        self.faults: dict = {}              # channel -> description
        self.snapshot_data: dict = {}
        self.gate_counts = {"admitted": 0, "rejected": 0, "out_of_band": 0}
        self.monitor_flags: Deque[dict] = deque(maxlen=50)
        self._last_flag_t: dict = {}
        self._seen_calls = 0
        self._last_baseline_log = -1e9
        self._last_targets: Optional[SupervisoryTargets] = None

    # ------------------------------------------------------------------ setup
    def _make_brain(self, kind: str):
        if kind == "ollama" and not ollama_available():
            self.log("brain", "Ollama / scram-operator unavailable: falling back to BaselineOperator")
            kind = "baseline"
        policy = OllamaOperator() if kind == "ollama" else BaselineOperator()
        self.brain_kind = kind
        self.policy = policy
        self._seen_calls = 0
        self.brain = AIOperator(policy=policy, authority_fn=lambda: self.authority)
        self.log("brain", f"brain is now {self.brain_label()}")

    def brain_label(self) -> str:
        if isinstance(self.policy, OllamaOperator):
            return f"Trained scram-operator (Ollama, every {self.policy.interval_s:g}s)"
        return "BaselineOperator (rule-based fallback)"

    def _connect(self, reset: bool) -> None:
        if reset:
            self.status = "resetting plant to the design point"
            self.log("plant", "restarting the simulator and waiting for it to settle")
            p = reset_and_settle()
            self.log("plant", f"settled at P={p.pressure:.1f} kPa, L={p.level:.2f}%")
        self.gate = ProvenanceGate(L1_KEY)
        self.svc = PlantService(self.host, self.port, limits=self.limits, gate=self.gate).start()
        fi.clear_all(self.svc.client)
        self.faults.clear()
        self.signer = CommandSigner(L1_KEY)
        self.l1 = LiveL1()
        self.monitor = BehaviorMonitor(MonitorLimits(level_low_pct=self.limits.level_low + 2.0))
        self._n_gate_events = 0
        self.state = self.svc.read()
        prime_l1(self.l1, self.state)
        self._first_signed: Optional[ControlCommand] = None
        self._was_tripped = False
        self.status = "running"

    # ------------------------------------------------------------- public API
    def start(self) -> "Engine":
        threading.Thread(target=self._run, name="scram-control", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def submit(self, action: str, **kw) -> str:
        fn = getattr(self, f"_act_{action}", None)
        if fn is None:
            raise ValueError(f"unknown action {action!r}")
        if action == "authority":           # cheap, thread-safe
            return fn(**kw)
        self._q.put(lambda: fn(**kw))
        return "queued"

    def now(self) -> float:
        return time.monotonic() - self.t0

    def log(self, kind: str, msg: str) -> None:
        with self._lock:
            self._ev_seq += 1
            self.events.append({"id": self._ev_seq, "t": round(self.now(), 1), "kind": kind, "msg": msg})

    # -------------------------------------------------------------- the loop
    def _run(self) -> None:
        try:
            self._make_brain(self.brain_kind)
            self._connect(self.want_reset)
        except Exception as e:                       # noqa: BLE001
            self.status, self.error = "error", f"{type(e).__name__}: {e}"
            self.log("error", f"startup failed: {self.error}")
            return
        self.log("plant", f"control loop running; SIS trips at P >= {self.limits.pressure_high:.0f} kPa, "
                          f"level <= {self.limits.level_low:.0f}% or >= {self.limits.level_high:.0f}%")
        last_hist = -1e9
        while not self._stop.is_set():
            try:
                while not self._q.empty():
                    self._q.get_nowait()()
                self._cycle()
            except (ConnectionError, OSError, ValueError) as e:
                self._reconnect(e)
                continue
            except Exception as e:                   # noqa: BLE001 - never kill the loop silently
                self.log("error", f"control cycle error: {type(e).__name__}: {e}")
                time.sleep(1.0)
                continue
            if self.now() - last_hist >= HISTORY_S:
                last_hist = self.now()
                self._record()
            time.sleep(POLL_S)

    def _reconnect(self, err: Exception) -> None:
        self.status = "plant connection lost; reconnecting"
        self.log("error", f"plant connection lost ({type(err).__name__}: {err}); reconnecting")
        while not self._stop.is_set():
            time.sleep(2.0)
            try:
                self.svc.client.close()
                self.svc.client = GrficsClient(self.host, self.port).connect()
                self.gate.seed(self.svc.client.read().get("state", {}))
                self.state = self.svc.read()
                prime_l1(self.l1, self.state)
                self.status = "running"
                self.log("plant", "reconnected to the plant")
                return
            except (ConnectionError, OSError, ValueError):
                continue

    def _cycle(self) -> None:
        state, t = self.state, self.now()
        self.cycles += 1
        targets = self.brain.step(state)
        self._last_targets = targets
        self._note_decision(state, targets, t)
        for f in self.monitor.observe(state, targets):
            key = " ".join(f.split(" ")[:3])          # same kind of flag
            if t - self._last_flag_t.get(key, -1e9) > 120:   # repeat at most every 2 min
                self._last_flag_t[key] = t
                self.monitor_flags.append({"t": round(t, 1), "msg": f})
                self.log("monitor", f"L2 monitor (measured only): {f}")

        cmd = self.signer.sign(self.l1.regulate(targets, state, dt=DT))
        stolen = self.scenarios.get("stolen_key")
        if stolen and not self.svc.safety.tripped:
            # attacker holds the L1 key: its commands are validly signed
            cmd = self.signer.sign(ControlCommand(f1_valve=100.0, f2_valve=100.0,
                                                  purge_valve=0.0, product_valve=0.0))
        self._first_signed = self._first_signed or cmd
        self.state = self.svc.apply(cmd)
        self._gate_bookkeeping(admitted=1)

        forged = self.scenarios.get("forged")
        if forged:
            if t >= forged["next"]:
                forged["next"] = t + 4.0
                self._inject_forged(forged)
            if t >= forged["until"]:
                del self.scenarios["forged"]
                self.log("attack", f"compromised agent stopped after {forged['n']} forged commands")

        tripped = self.svc.safety.tripped
        if tripped and not self._was_tripped:
            self.log("sis", f"SIS TRIP: {self.svc.safety.reason} (true values) | measured view: "
                            f"P={self.state.pressure:.0f} kPa, L={self.state.level:.1f}% | "
                            f"safe state: {'product valve closed, feeds held' if self.svc.safety.cause == 'level_low' else 'e_stop (feeds shut, purge + product open)'}")
            if stolen:
                del self.scenarios["stolen_key"]
                self.log("attack", "stolen-key attacker's commands are now overridden by the interlock")
        self._was_tripped = tripped

    def _note_decision(self, state: PlantState, targets: SupervisoryTargets, t: float) -> None:
        p = self.policy
        if isinstance(p, LLMOperator):
            if p.calls > self._seen_calls:
                self._seen_calls = p.calls
                e = dict(p.log[-1])
                truth = state.truth or {}
                self.decisions.append({**e, "t": round(t, 1), "brain": "scram-operator",
                                       "true": {"pressure": _num(truth.get("pressure")),
                                                "level": _num(truth.get("liquid_level"))}})
        elif t - self._last_baseline_log >= 5.0:
            self._last_baseline_log = t
            truth = state.truth or {}
            self.decisions.append({"t": round(t, 1), "brain": "baseline", "outcome": "ok",
                                   "sp": [targets.pressure_sp, targets.level_sp,
                                          targets.production_rate_sp, targets.a_comp_sp],
                                   "saw": {"pressure": round(state.pressure, 1), "level": round(state.level, 2)},
                                   "true": {"pressure": _num(truth.get("pressure")),
                                            "level": _num(truth.get("liquid_level"))},
                                   "rationale": targets.rationale, "latency_s": 0.0})

    def _gate_bookkeeping(self, admitted: int = 0) -> None:
        new = self.gate.events[self._n_gate_events:]
        self._n_gate_events = len(self.gate.events)
        # keep the gate's own event list bounded on a long run
        if len(self.gate.events) > 1000:
            del self.gate.events[:500]
            self._n_gate_events = len(self.gate.events)
        rej = sum(e.kind == "rejected_command" for e in new)
        oob = [e for e in new if e.kind == "out_of_band_write"]
        self.gate_counts["admitted"] += admitted - rej
        self.gate_counts["rejected"] += rej
        self.gate_counts["out_of_band"] += len(oob)
        for e in oob:
            self.log("gate", f"out-of-band write detected and overwritten: {e.detail}")
        return new

    def _inject_forged(self, forged: dict) -> None:
        kinds = ("unsigned", "wrong-key", "replay")
        kind = kinds[forged["n"] % 3]
        forged["n"] += 1
        if kind == "unsigned":
            bad = ControlCommand(f1_valve=100.0, f2_valve=100.0, purge_valve=0.0, product_valve=100.0)
        elif kind == "wrong-key":
            bad = replace(CommandSigner(ATTACKER_KEY).sign(
                ControlCommand(f1_valve=0.0, f2_valve=0.0, purge_valve=100.0, product_valve=100.0)),
                seq=10_000_000)
        else:
            bad = self._first_signed
        self.state = self.svc.apply(bad)
        new = self._gate_bookkeeping(admitted=1)
        rej = [e for e in new if e.kind == "rejected_command"]
        self.log("gate", f"compromised agent sent a {kind} command -> "
                         + (f"REJECTED ({rej[0].detail}); last-good re-asserted" if rej else "ADMITTED"))

    def _record(self) -> None:
        s, tg = self.state, self._last_targets
        tr = s.truth or {}
        with self._lock:
            self._hist_seq += 1
            self.history.append([self._hist_seq, round(self.now(), 1),
                                 _num(s.pressure), _num(tr.get("pressure")),
                                 _num(s.level), _num(tr.get("liquid_level")),
                                 _num(s.f1_valve_pos), _num(s.f2_valve_pos),
                                 _num(s.purge_valve_pos), _num(s.product_valve_pos),
                                 _num(tg.pressure_sp) if tg else None, _num(tg.level_sp) if tg else None,
                                 int(self.svc.safety.tripped)])
            self.snapshot_data = self._snapshot()

    def _snapshot(self) -> dict:
        s, tg, saf = self.state, self._last_targets, self.svc.safety
        tr = s.truth or {}
        lim = self.limits
        return {
            "t": round(self.now(), 1), "status": self.status, "error": self.error, "cycles": self.cycles,
            "brain": {"kind": self.brain_kind, "label": self.brain_label(), "authority": self.authority,
                      "inflight": bool(getattr(self.policy, "_inflight", False)),
                      "calls": getattr(self.policy, "calls", None)},
            "measured": {"pressure": _num(s.pressure), "level": _num(s.level),
                         "product_flow": _num(s.product_flow), "a_comp_pct": _num(s.a_in_purge * 100),
                         "f1_flow": _num(s.f1_flow), "f2_flow": _num(s.f2_flow), "purge_flow": _num(s.purge_flow)},
            "truth": {"pressure": _num(tr.get("pressure")), "level": _num(tr.get("liquid_level")),
                      "product_flow": _num(tr.get("product_flow"))},
            "valves": {"f1": _num(s.f1_valve_pos), "f2": _num(s.f2_valve_pos),
                       "purge": _num(s.purge_valve_pos), "product": _num(s.product_valve_pos)},
            "targets": None if tg is None else {
                "pressure_sp": _num(tg.pressure_sp), "level_sp": _num(tg.level_sp),
                "production_rate_sp": _num(tg.production_rate_sp), "a_comp_sp": _num(tg.a_comp_sp),
                "authority": tg.authority, "rationale": tg.rationale},
            "sis": {"tripped": saf.tripped, "reason": saf.reason, "cause": saf.cause,
                    "can_reset": saf.tripped and saf.can_reset(tr.get("pressure", 0.0), tr.get("liquid_level", 0.0)),
                    "limits": {"pressure_high": lim.pressure_high, "pressure_rearm": lim.pressure_rearm,
                               "level_low": lim.level_low, "level_low_rearm": lim.level_low_rearm,
                               "level_high": lim.level_high, "level_high_rearm": lim.level_high_rearm}},
            "gate": dict(self.gate_counts),
            "scenarios": {k: {kk: (round(vv, 1) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                          for k, v in self.scenarios.items()},
            "faults": dict(self.faults),
            "decision": self.decisions[-1] if self.decisions else None,
        }

    def read(self, since_hist: int = 0, since_event: int = 0, n_decisions: int = 12) -> dict:
        with self._lock:
            snap = dict(self.snapshot_data) or {"status": self.status, "error": self.error, "t": round(self.now(), 1)}
            snap["history"] = [h for h in self.history if h[0] > since_hist]
            snap["events"] = [e for e in self.events if e["id"] > since_event]
            snap["decisions"] = list(self.decisions)[-n_decisions:]
        return snap

    # ---------------------------------------------------------------- actions
    def _act_authority(self, value: float) -> str:
        self.authority = max(0.0, min(1.0, float(value)))
        self.log("governance", f"authority set to {self.authority:.2f}")
        return "ok"

    def _act_brain(self, kind: str) -> None:
        self._make_brain(kind)

    def _act_level_drift(self) -> None:
        fi.drift(self.svc.client, "tank_level", DRIFT_RATE_PCT_PER_HR)
        self.faults["tank_level"] = f"drift +{DRIFT_RATE_PCT_PER_HR:g}%/h"
        self.log("attack", "level transmitter starts drifting high: the model and L1 see a rising level, "
                           "L1 opens the product valve, the TRUE level falls")

    def _act_pressure_dropout(self) -> None:
        fi.dropout(self.svc.client, "tank_pressure", 0.5)
        self.faults["tank_pressure"] = "dropout 50% (reads 0)"
        self.log("attack", "pressure transmitter drops out (reads 0 half of every 10 s): "
                           "the trained model should refuse to act on implausible readings")

    def _act_forged(self, duration_s: float = 60.0) -> None:
        t = self.now()
        self.scenarios["forged"] = {"until": t + duration_s, "next": t, "n": 0}
        self.log("attack", f"compromised agent starts injecting forged valve commands for {duration_s:.0f}s "
                           "(unsigned / wrong key / replayed)")

    def _act_stolen_key(self) -> None:
        self.scenarios["stolen_key"] = {"since": self.now()}
        self.log("attack", "attacker with the STOLEN L1 key takes over: feeds 100%, purge and product shut. "
                           "Its commands pass the provenance gate; only physics can stop it")

    def _act_oob_write(self) -> None:
        with GrficsClient(self.host, self.port) as c:
            c.write_inputs({"product_valve_sp": 100.0, "purge_valve_sp": 100.0})
        self.log("attack", "attacker writes valves straight to the plant (:55555), bypassing the enforcer: "
                           "product and purge 100%")

    def _act_clear_faults(self) -> None:
        fi.clear_all(self.svc.client)
        self.faults.clear()
        self.scenarios.pop("forged", None)
        if self.scenarios.pop("stolen_key", None):
            self.log("attack", "stolen-key attacker stopped")
        self.log("plant", "all sensor faults cleared; attacks stopped")

    def _act_reset_interlock(self) -> None:
        if not self.svc.safety.tripped:
            self.log("sis", "interlock is not tripped")
        elif self.svc.reset_interlock():
            v = DESIGN_VALVES
            self.state = self.svc.apply(self.signer.sign(ControlCommand(
                f1_valve=v["f1"], f2_valve=v["f2"], purge_valve=v["purge"], product_valve=v["product"])))
            prime_l1(self.l1, self.state)
            self.log("sis", "interlock RE-ARMED by operator (safety channel back inside the re-arm band); "
                            "restarting from the design valve lineup, then the AI and L1 take over")
        else:
            tr = self.state.truth or {}
            self.log("sis", f"reset REFUSED: safety channel not in the re-arm band yet "
                            f"(P {tr.get('pressure', 0):.0f} kPa, L {tr.get('liquid_level', 0):.1f}%)")

    def _act_reset_plant(self) -> None:
        try:
            self.svc.stop()
        except Exception:                            # noqa: BLE001
            pass
        self.scenarios.clear()
        try:
            self._connect(reset=True)
        except Exception as e:                       # noqa: BLE001
            self.log("error", f"plant reset failed ({type(e).__name__}: {e}); reattaching as-is")
            try:
                self._connect(reset=False)
            except Exception as e2:                  # noqa: BLE001
                # let the control loop's reconnect handler keep retrying
                raise ConnectionError(f"plant unreachable after failed reset: {e2}") from e2
        self._make_brain(self.brain_kind)
