"""
L0 plant service.

This is the clean boundary the rest of SCRAM sees. It:
  * wraps the GRFICS socket client,
  * translates raw simulator JSON into the shared PlantState contract,
  * runs the independent SafetyInterlock every cycle and, when tripped,
    overrides the incoming ControlCommand to force the plant safe.

Everything above L0 talks to this class, never to GrficsClient directly.

Note on the real/measured split: the simulator reports both the true physical
values ("outputs") and the sensor-faulted values ("measured"). L0 publishes the
MEASURED values as the live state (that is what a PLC/HMI really sees) and
tucks the truth into PlantState.truth for eval only.

The safety interlock is the one exception: it evaluates "outputs", modeled as
a dedicated safety-instrumented sensor path independent of the basic control
system, not "measured" (the basic control system's own instrumentation). A
fault injected via l0_plant/fault_injection.py only ever touches "measured",
so it can corrupt what L1/L2/L3 see in PlantState without being able to stop
the interlock from tripping. This assumes standard SIS independence: the
safety-instrumented path is out of the attacker's reach - see ARCHITECTURE.md.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import PlantState, ControlCommand          # noqa: E402
from l0_plant.grfics_client import GrficsClient            # noqa: E402
from l0_plant.safety import SafetyInterlock, TripLimits    # noqa: E402
from l0_plant.provenance_gate import ProvenanceGate        # noqa: E402


class PlantService:
    def __init__(self, host: str = "127.0.0.1", port: int = 55555,
                 limits: TripLimits | None = None,
                 gate: ProvenanceGate | None = None):
        self.client = GrficsClient(host, port)
        self.safety = SafetyInterlock(limits)
        # Optional command-origin gate (second ground of the veto). None =
        # legacy behaviour: every command is trusted. See provenance_gate.py.
        self.gate = gate
        self._connected = False

    def start(self) -> "PlantService":
        self.client.connect()
        self._connected = True
        if self.gate is not None and self.gate.last_good is None:
            self.gate.seed(self.client.read().get("state", {}))
        return self

    def stop(self) -> None:
        self.client.close()
        self._connected = False

    # ---- state translation ---------------------------------------------------
    def _to_state(self, raw: dict) -> PlantState:
        m = raw.get("measured", {})
        s = raw.get("state", {})
        o = raw.get("outputs", {})
        # Run the interlock on the SIS channel (outputs), not measured: a
        # sensor fault injected via fault_injection.py corrupts "measured"
        # only, so it can mislead L1/L2/L3 but can never stop or delay a real
        # trip here (assumes standard SIS independence - see ARCHITECTURE.md).
        scrammed = self.safety.evaluate(
            pressure=o.get("pressure", 0.0),
            level=o.get("liquid_level", 0.0),
        )
        return PlantState(
            pressure=m.get("pressure", 0.0),
            level=m.get("liquid_level", 0.0),
            f1_flow=m.get("f1_flow", 0.0),
            f2_flow=m.get("f2_flow", 0.0),
            purge_flow=m.get("purge_flow", 0.0),
            product_flow=m.get("product_flow", 0.0),
            # NB: the simulator capitalizes the composition keys in "measured"
            a_in_purge=m.get("A_in_purge", 0.0),
            b_in_purge=m.get("B_in_purge", 0.0),
            c_in_purge=m.get("C_in_purge", 0.0),
            f1_valve_pos=s.get("f1_valve_pos", 0.0),
            f2_valve_pos=s.get("f2_valve_pos", 0.0),
            purge_valve_pos=s.get("purge_valve_pos", 0.0),
            product_valve_pos=s.get("product_valve_pos", 0.0),
            cost=o.get("cost", 0.0),
            scrammed=scrammed,
            scram_reason=self.safety.reason,
            truth=o or None,
        )

    # ---- public API ----------------------------------------------------------
    def read(self) -> PlantState:
        return self._to_state(self.client.read())

    def apply(self, command: ControlCommand) -> PlantState:
        """
        Apply an L1 control command. If the interlock is tripped, the command
        is ignored and the plant is driven to its safe state instead. The AI
        has no way to override this path - that is the whole point.
        """
        gate = self.gate
        if gate is not None:
            # Anything on the plant that the enforcer didn't write is, by
            # construction, an out-of-band write: record it; the write below
            # re-asserts last-good over it.
            gate.audit(self.client.read().get("state", {}))
            ok, _why = gate.admit(command)
            # rejected -> fall back to the last-good command, not the injected one
            command = command if ok else gate.last_good
        # A non-finite valve value is never applied: fall back to last-good, or
        # hold the valves where they are (not writing a setpoint holds it).
        if not command.is_finite():
            command = gate.last_good if gate is not None and gate.last_good is not None else None
        cmd = command.clamped() if command is not None else None
        if self.safety.tripped:
            # Force-safe, cause-specific (SafetyInterlock.safe_state_inputs):
            # e_stop for pressure/high-level, product closed for low level.
            inputs = self.safety.safe_state_inputs()
            if gate is not None:
                gate.note_safe_state(inputs)
            raw = self.client.write_inputs(inputs)
        else:
            if gate is not None:
                gate.expect_e_stop = False
            valves = {} if cmd is None else {
                "f1_valve_sp": cmd.f1_valve,
                "f2_valve_sp": cmd.f2_valve,
                "purge_valve_sp": cmd.purge_valve,
                "product_valve_sp": cmd.product_valve,
            }
            raw = self.client.write_inputs({**valves, "e_stop": 0})
        return self._to_state(raw)

    def reset_interlock(self) -> bool:
        """
        Deliberate operator reset; only clears if the SIS channel is back
        inside the re-arm band. Reads outputs directly (not
        PlantState.pressure/level, which are measured and could be faulted)
        for the same reason the trip itself reads the SIS channel: re-arm
        must not be spoofable either.
        """
        raw = self.client.read()
        o = raw.get("outputs", {})
        return self.safety.reset(o.get("pressure", 0.0), o.get("liquid_level", 0.0))
