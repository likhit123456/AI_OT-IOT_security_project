"""
SCRAM cross-layer contracts.

This module is the single source of truth for the data that crosses the
boundaries between the Purdue levels. Every layer imports its types from here
so that L0/L1/L2/L3 can be developed independently and still interoperate.

Ownership:
    L0 (plant)        - you
    L1 (control)      - team
    L2 (supervisory)  - team
    L3 (brain)        - you

Data flow (one control cycle):

    L3.decide(state) --> SupervisoryTargets
          |  (governance clamps authority here, L1/L2 owned)
          v
    L1.regulate(targets, state) --> ControlCommand   (valve positions)
          |
          v
    L0.apply(command) --> PlantState                 (next measured state)

Units are physical engineering units, NOT the 0-65535 Modbus scaling. The L0
service is the only place that knows about raw register scaling; everything
above L0 works in real units.
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field, asdict, replace
from typing import Optional
import time


# ----- valve / actuator identifiers -------------------------------------------
# The GRFICS Tennessee-Eastman reactor exposes four manipulated valves.
VALVES = ("f1", "f2", "purge", "product")


@dataclass(frozen=True)
class PlantState:
    """
    What L0 reports upward. This is the MEASURED view (what a PLC/HMI would
    actually see) plus actuator feedback. L3 and L2 consume this.

    `truth` carries the un-faulted physical values. L0 fills it so the Guardian
    (L2) can be scored against ground truth during development, but production
    detection logic must rely only on the measured fields - treat `truth` as a
    debug/eval channel, not a signal source.
    """
    # measured process variables (physical units)
    pressure: float            # kPa
    level: float               # % full
    f1_flow: float             # kmol/h
    f2_flow: float             # kmol/h
    purge_flow: float          # kmol/h
    product_flow: float        # kmol/h
    a_in_purge: float          # mol %
    b_in_purge: float          # mol %
    c_in_purge: float          # mol %
    # actuator feedback (commanded position the plant currently holds)
    f1_valve_pos: float        # %
    f2_valve_pos: float        # %
    purge_valve_pos: float     # %
    product_valve_pos: float   # %
    # operating cost figure of merit from the model
    cost: float
    # safety / interlock status set by L0's hardware safety layer
    scrammed: bool = False     # True => interlock has tripped, plant is in safe state
    scram_reason: str = ""
    # eval-only ground truth (see docstring); None in a hardware deployment
    truth: Optional[dict] = None
    # wall-clock stamp for logging / sequencing
    t: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SupervisoryTargets:
    """
    L3's output. The AI operator expresses INTENT as setpoints, never as valve
    positions. L1 is responsible for regulating the plant to these targets.

    `authority` is a 0..1 scalar the governance layer (teammate) writes to
    advertise how much freedom the brain currently has; L3 reads it so it can
    behave conservatively when de-authorized, but L1/governance is what
    actually enforces the clamp. Fields left None mean "no change / hold".
    """
    pressure_sp: Optional[float] = None       # kPa
    level_sp: Optional[float] = None          # %
    production_rate_sp: Optional[float] = None  # product kmol/h
    a_comp_sp: Optional[float] = None         # target A in purge, mol %
    authority: float = 1.0
    rationale: str = ""                        # human-readable why, for HMI/logs

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ControlCommand:
    """
    L1's output and L0's input: absolute valve positions in percent [0,100].
    L0 clamps these to the physical range and may override them entirely when
    the safety interlock is tripped.
    """
    f1_valve: float
    f2_valve: float
    purge_valve: float
    product_valve: float
    # Command-origin provenance (l0_plant/provenance_gate.py). ADDITIVE and
    # backward-compatible: both default to "unsigned", and a PlantService
    # with no gate configured treats every command as trusted (the previous
    # behaviour), so existing L1 code and tests are unaffected.
    # NOTE: this is a change to the L1 seam. The L1 teammate must confirm it
    # (L1 would sign each command via CommandSigner.sign before handing it to
    # L0) before the gate is switched on outside experiments.
    seq: int = 0
    auth_tag: str = ""

    def is_finite(self) -> bool:
        return all(math.isfinite(float(v)) for v in
                   (self.f1_valve, self.f2_valve, self.purge_valve, self.product_valve))

    def clamped(self) -> "ControlCommand":
        """Clamp to [0, 100]. A non-finite value raises: NaN would otherwise
        clamp to 100% open (min(100, nan) is 100). Callers check is_finite()."""
        if not self.is_finite():
            raise ValueError(f"non-finite valve value in {self}")
        c = lambda x: max(0.0, min(100.0, float(x)))
        return replace(self, f1_valve=c(self.f1_valve), f2_valve=c(self.f2_valve),
                       purge_valve=c(self.purge_valve), product_valve=c(self.product_valve))

    def as_dict(self) -> dict:
        return asdict(self)
