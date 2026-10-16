"""
L0 fault-injection helpers.

Thin wrappers around the reactor's OWN documented sensor-fault interface
(see sim/TE_process.cc's apply_sensor_fault / simulation/simulation/TE_process.cc
in the running image). The simulator already exposes, as write-inputs, a fault
mode and severity for each sensor channel, and already reports both the
un-faulted value ("outputs") and the resulting faulted value ("measured") in
its JSON. These helpers just name the modes and channels so callers don't have
to remember magic numbers - there is no corruption logic here; all of it lives
in the simulator.

Modes (from TE_process.cc):
    NONE    (0)  healthy - measured tracks outputs
    FROZEN  (1)  measured holds at whatever it last was
    DRIFT   (2)  measured accumulates an offset at `severity` units/hour
    NOISE   (3)  measured = outputs +/- uniform(severity)
    DROPOUT (4)  measured reads 0 for a `severity` fraction of each 10s window

Channels: tank_pressure, tank_level, f1_flow, f2_flow, purge_flow,
product_flow, analyzer (the analyzer channel covers A/B/C composition
together, as a real GC samples them as one instrument).

The safety interlock (l0_plant/safety.py) never reads "measured" - it reads
"outputs" (truth) via PlantState.truth, so nothing set here can affect it.
"""

from __future__ import annotations

from enum import IntEnum

from l0_plant.grfics_client import GrficsClient


class FaultMode(IntEnum):
    NONE = 0
    FROZEN = 1
    DRIFT = 2
    NOISE = 3
    DROPOUT = 4


CHANNELS = (
    "tank_pressure",
    "tank_level",
    "f1_flow",
    "f2_flow",
    "purge_flow",
    "product_flow",
    "analyzer",
)


def set_fault(client: GrficsClient, channel: str, mode: FaultMode,
              severity: float = 0.0) -> dict:
    """Write a channel's fault mode/severity. Returns the resulting state."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown fault channel {channel!r}; expected one of {CHANNELS}")
    return client.write_inputs({
        f"{channel}_fault_mode": int(mode),
        f"{channel}_fault_severity": float(severity),
    })


def freeze(client: GrficsClient, channel: str) -> dict:
    """Measured holds at its current reading regardless of how truth moves."""
    return set_fault(client, channel, FaultMode.FROZEN)


def drift(client: GrficsClient, channel: str, rate_per_hour: float) -> dict:
    """Measured accumulates an offset from truth at rate_per_hour units/hour."""
    return set_fault(client, channel, FaultMode.DRIFT, rate_per_hour)


def noise(client: GrficsClient, channel: str, amplitude: float) -> dict:
    """Measured = truth +/- uniform(amplitude) each tick."""
    return set_fault(client, channel, FaultMode.NOISE, amplitude)


def dropout(client: GrficsClient, channel: str, duty: float) -> dict:
    """Measured reads 0 for a `duty` fraction (0..1) of each 10s window."""
    return set_fault(client, channel, FaultMode.DROPOUT, duty)


def clear(client: GrficsClient, channel: str) -> dict:
    """Return a channel to healthy (measured tracks truth again)."""
    return set_fault(client, channel, FaultMode.NONE)


def clear_all(client: GrficsClient) -> dict:
    """Clear every channel's fault. Returns the final state."""
    raw = {}
    for ch in CHANNELS:
        raw = clear(client, ch)
    return raw
