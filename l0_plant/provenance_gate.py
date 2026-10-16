"""
Command-origin integrity on the actuation path (defensive).

The enforcer (PlantService) is the ONLY sanctioned writer to the plant
(port 55555). The physics interlock (safety.py) grounds its veto in the
process state; this module is the second ground: WHO issued the command. It
catches the case physics cannot - a command that is individually within every
physical limit (so the SIS never trips) but was not issued by the authorized
path.

Two mechanisms, with different jobs:

1. Provenance check (commands that reach the enforcer). L1 signs each command
   with a shared key (HMAC-SHA256 over a strictly increasing sequence number
   plus the four valve positions). `ProvenanceGate.admit` rejects a command
   with a missing/invalid tag or a replayed/stale sequence number, and the
   enforcer re-asserts the last-good command instead.

2. Readback audit (commands that DO NOT reach the enforcer). A write sent
   straight to the plant socket never passes through admit(), so it can't be
   prevented from here - the plant's JSON protocol has no authentication.
   What the enforcer can do is notice: it knows exactly what it last wrote, so
   any valve position or e_stop state on the plant that differs from that is,
   by construction, someone else's write. `ProvenanceGate.audit` flags the
   mismatch and the enforcer re-asserts last-good on its next write. Limits:
   detection is per control cycle, so an out-of-band write can dwell for up
   to one cycle; it is detected and bounded, not prevented. Preventing it
   needs network-level controls (only the enforcer host may reach 55555) -
   outside this code.

Key handling: the key is supplied by the caller (e.g. from the environment);
nothing here ships a default key.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from contracts import ControlCommand

VALVE_KEYS = ("f1_valve_pos", "f2_valve_pos", "purge_valve_pos", "product_valve_pos")
AUDIT_TOLERANCE_PCT = 0.5


def _tag(key: bytes, seq: int, c: ControlCommand) -> str:
    msg = f"{seq}|{c.f1_valve:.4f}|{c.f2_valve:.4f}|{c.purge_valve:.4f}|{c.product_valve:.4f}"
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


class CommandSigner:
    """L1-side: stamps each outgoing command with a sequence number and tag."""

    def __init__(self, key: bytes):
        self._key = key
        self._seq = 0

    def sign(self, cmd: ControlCommand) -> ControlCommand:
        self._seq += 1
        return replace(cmd, seq=self._seq, auth_tag=_tag(self._key, self._seq, cmd))


@dataclass
class GateEvent:
    kind: str        # "rejected_command" | "out_of_band_write"
    detail: str


@dataclass
class ProvenanceGate:
    key: bytes
    # strict=False reproduces the legacy "default = trusted" behaviour for
    # unsigned commands (a signed command with a bad tag is still rejected).
    strict: bool = True
    last_seq: int = 0
    last_good: Optional[ControlCommand] = None
    expect_e_stop: bool = False
    events: List[GateEvent] = field(default_factory=list)

    def seed(self, valves: Dict[str, float]) -> None:
        """Initial last-good = what the plant holds when the enforcer takes over."""
        self.last_good = ControlCommand(valves["f1_valve_pos"], valves["f2_valve_pos"],
                                        valves["purge_valve_pos"], valves["product_valve_pos"])

    def admit(self, cmd: ControlCommand) -> Tuple[bool, str]:
        if not cmd.is_finite():
            return self._reject("non-finite valve value")
        if not cmd.auth_tag:
            if self.strict:
                return self._reject("unsigned command (no provenance tag)")
            return self._accept(cmd)
        if cmd.seq <= self.last_seq:
            return self._reject(f"replayed/stale sequence {cmd.seq} (last {self.last_seq})")
        if not hmac.compare_digest(cmd.auth_tag, _tag(self.key, cmd.seq, cmd)):
            return self._reject("invalid provenance tag")
        self.last_seq = cmd.seq
        return self._accept(cmd)

    def _accept(self, cmd: ControlCommand) -> Tuple[bool, str]:
        self.last_good = replace(cmd.clamped(), seq=0, auth_tag="")
        return True, "ok"

    def _reject(self, why: str) -> Tuple[bool, str]:
        self.events.append(GateEvent("rejected_command", why))
        return False, why

    def note_safe_state(self, inputs: dict) -> None:
        """
        Record what the interlock's cause-specific safe state wrote, so the
        audit expects it (e_stop for pressure/high level; product closed with
        the other valves held for low level).
        """
        self.expect_e_stop = bool(inputs.get("e_stop", 0))
        if not self.expect_e_stop and self.last_good is not None and "product_valve_sp" in inputs:
            self.last_good = replace(self.last_good, product_valve=float(inputs["product_valve_sp"]))

    def audit(self, plant_state: dict) -> List[str]:
        """
        Compare the plant's own readback (raw["state"]) with what the enforcer
        last wrote. Returns the mismatches (empty = clean). Valve positions
        are not compared while e_stop is the expected state, since e_stop
        deliberately overrides them.
        """
        found: List[str] = []
        if bool(plant_state.get("e_stop", False)) != self.expect_e_stop:
            found.append(f"e_stop is {plant_state.get('e_stop')} but enforcer expects {self.expect_e_stop}")
        if not self.expect_e_stop and self.last_good is not None:
            want = (self.last_good.f1_valve, self.last_good.f2_valve,
                    self.last_good.purge_valve, self.last_good.product_valve)
            for k, w in zip(VALVE_KEYS, want):
                got = float(plant_state.get(k, w))
                if abs(got - w) > AUDIT_TOLERANCE_PCT:
                    found.append(f"{k} is {got:.1f}% but enforcer last wrote {w:.1f}%")
        for f in found:
            self.events.append(GateEvent("out_of_band_write", f))
        return found
