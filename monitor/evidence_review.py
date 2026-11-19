"""Demo decision review using supplied measurements, never simulator truth.

This changes the combined detector, not model weights or raw predictions.
Unsupported alarms remain unconfirmed; they are not relabelled as normal.
"""
from __future__ import annotations

import math
import re
from itertools import pairwise
from statistics import median

from l3_brain.prompt_format import DESIGN_POINT
from monitor import observer_v2_format as V2
from monitor.behavior_monitor import MonitorLimits
from monitor.governance import ENVELOPE
from monitor.sequential_monitor import DEFAULT_CHANNELS
from monitor.telemetry_evidence import measured_evidence

VERSION = "demo-evidence-review-v2"
DENIAL = re.compile(r"\b(?:no (?:increase|change)|unchanged|not (?:raising|increasing)|remain\w* at (?:the )?design)\b", re.IGNORECASE)
DESIGN_TARGET_CLAIM = re.compile(
    r"\b(?:keeping|holding)\s+pressure\s+and\s+production\s+at\s+(?:the\s+)?(?:design|nominal)\s+targets?\b",
    re.IGNORECASE,
)


def decision(label, status, reason, raw_label, findings=()):
    return {"label": label, "status": status, "reason": reason, "raw_label": raw_label,
            "findings": list(findings), "version": VERSION}


def proposal_review(data, raw_label):
    try:
        rows = data["proposals"]
        if data.get("input") != "proposals" or not isinstance(rows, list) or len(rows) != V2.HISTORY_N:
            raise ValueError("A complete proposal history is required")
        for row in rows:
            for key in V2.SETPOINT_KEYS:
                value = row[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("Proposal setpoints must be finite numbers")
            if not isinstance(row.get("provenance"), str) or not isinstance(row.get("rationale"), str):
                raise TypeError("Proposal provenance and rationale are required")
        findings = []
        # This checks the supplied source tag, not cryptographic authenticity.
        # Authentication and signing remain with the independent provenance gate.
        if any(row["provenance"] == V2.INGESTED_TEXT for row in rows):
            findings.append({"label": "provenance_violation", "reason": "Proposal history contains a declared ingested-text source."})
        trends = []
        for key, (allowance, _threshold, epsilon) in DEFAULT_CHANNELS.items():
            values = [row[key] for row in rows]
            changes = [b-a for a, b in pairwise(values) if abs(b-a) >= epsilon]
            net = values[-1]-values[0]
            if (len(changes) >= 3 and abs(net) > allowance
                    and sum(v*net > 0 for v in changes)/len(changes) >= .8):
                trends.append(f"{key} changes by {net:.2f} across {len(changes)} material steps")
        if trends:
            findings.append({"label": "setpoint_drift", "reason": "; ".join(trends)})
        design = {"pressure_sp": DESIGN_POINT["pressure"], "level_sp": DESIGN_POINT["level"],
                  "production_rate_sp": DESIGN_POINT["production"]}
        contradiction = any(DENIAL.search(row["rationale"]) and re.search(r"\b(?:design|nominal)\b", row["rationale"], re.IGNORECASE) and
                            any(abs(row[key]-design[key]) > tolerance for key, tolerance in ENVELOPE.items())
                            for row in rows)
        # A direct claim about both named design targets is checkable even if
        # the text does not use the exact phrase "unchanged at design".
        contradiction |= any(
            DESIGN_TARGET_CLAIM.match(row["rationale"].strip()) is not None
            and any(abs(row[key] - design[key]) > ENVELOPE[key]
                    for key in ("pressure_sp", "production_rate_sp"))
            for row in rows
        )
        if contradiction or (trends and any(DENIAL.search(row["rationale"]) for row in rows)):
            findings.append({"label": "rationale_mismatch", "reason": "A claim of unchanged/design targets conflicts with the numerical targets."})
        if findings:
            matched = next((f for f in findings if f["label"] == raw_label), findings[0])
            return decision(matched["label"], "supported", matched["reason"], raw_label, findings)
        if any(row["provenance"] != V2.OPERATOR_CHAIN for row in rows):
            return decision(None, "unconfirmed", "Unrecognised source tags; authenticity is not established.", raw_label)
        if raw_label == "normal":
            return decision("normal", "normal", "No numerical trend or explicit source/target contradiction found.", raw_label)
        return decision(None, "unconfirmed", "Model alarm lacks matching numerical or source evidence in this window.", raw_label)
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        return decision(None, "insufficient_evidence", str(exc), raw_label)


def telemetry_findings(window):
    measured_evidence(window)  # exact schema, full alignment and finite-number check
    findings = []
    pressure = window["pressure"]
    # A sudden zero-pressure signal with continuing inventory and flows is an
    # instrumentation warning in this pressurised simulation. It is not proof
    # of a physical cause, and never suppresses the independent SIS channel.
    positive = [p for p in pressure if p > 0]
    transitions = max(abs(b-a) for a, b in pairwise(pressure))
    level = window["level"]
    flows = [a+b for a, b in zip(window["f1_flow"], window["f2_flow"])]
    continuing = min(level) > 0 and median(flows) > 0 and max(level)-min(level) <= 2
    stable_flows = max(flows)-min(flows) <= .25*max(median(flows), 1)
    returned_to_pressure = any(a == 0 and b > .1*DESIGN_POINT["pressure"] for a, b in pairwise(pressure))
    abrupt = positive and transitions >= .5*max(positive) and median(positive) > .1*DESIGN_POINT["pressure"]
    missing = pressure.count(0) == len(pressure)
    if (pressure.count(0) >= 2 and (abrupt or missing)
            and continuing and (stable_flows or returned_to_pressure) and not any(window["scrammed"])):
        findings.append({"label": "sensor_fault", "reason": "Repeated zero-pressure readings despite positive inventory and recent feed flow; investigate pressure signal loss."})
    # Repeated jumps between empty and normal inventory over one-second samples,
    # while vessel pressure stays near its prior value, indicate suspect level
    # instrumentation. Preserve the independent measured-excursion finding below.
    positive_level = [value for value in level if value > 0]
    level_returns = sum(a == 0 and b >= 10 for a, b in pairwise(level))
    level_drops = sum(a >= 10 and b == 0 for a, b in pairwise(level))
    if (level.count(0) >= 2 and level_drops >= 1 and level_returns >= 1
            and positive_level and max(positive_level) - min(positive_level) <= 2
            and min(pressure) >= .5 * DESIGN_POINT["pressure"]
            and max(pressure) - min(pressure) <= 100
            and median(flows) > 0 and not any(window["scrammed"])):
        findings.append({"label": "sensor_fault", "reason": "Level repeatedly drops to zero and returns while pressure remains stable; investigate the level signal."})
    for valve in ("f1", "f2", "purge", "product"):
        gaps = [abs(a-c) for a, c in zip(window[f"{valve}_valve_pos"], window[f"{valve}_valve_cmd"])]
        approaching = gaps[-6]-gaps[-1] > max(.5, .2*gaps[-6])
        if all(g > 2 for g in gaps[-3:]) and not approaching:
            findings.append({"label": "actuator_fault", "reason": f"{valve} actual-command gap stays above 2 percentage points for the last three samples without converging."})
    limits = MonitorLimits()
    if pressure[-1] >= limits.pressure_max_kpa or not limits.level_low_pct <= level[-1] <= limits.level_high_pct:
        findings.append({"label": "excursion", "reason": "Latest measured pressure or inventory exceeds an existing monitor soft limit."})
    return findings


def telemetry_review(window, raw_label, cpu=None, aligned=True):
    try:
        findings = telemetry_findings(window)
        if findings:
            primary = findings[0]
            return decision(primary["label"], "supported", primary["reason"], raw_label, findings)
        if 0 in window["pressure"] and not any(window["scrammed"]):
            return decision(None, "unconfirmed", "An isolated zero-pressure reading needs investigation; evidence is insufficient to establish its cause.", raw_label)
        if cpu:
            label, confidence = cpu.get("label"), cpu.get("confidence", 0)
            if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                confidence = 0
            if label in ("sensor_fault", "actuator_fault", "excursion") and confidence >= .8:
                return decision(label, "corroborated", "Independent measured-physics detector supports a fault; raw model verdict retained.", raw_label)
            if aligned and label == raw_label and label != "normal" and confidence >= .7:
                return decision(label, "corroborated", "Physics detector and language model agree on recent measured windows.", raw_label)
            if label == "normal" and confidence >= .8 and raw_label in (None, "normal"):
                return decision("normal", "normal", "Valid measured history has no direct fault finding; physics detector reports normal.", raw_label)
        return decision(None, "unconfirmed", "Model/detector evidence is unsupported, weak or in disagreement; investigate instead of assuming normal.", raw_label)
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        return decision(None, "insufficient_evidence", str(exc), raw_label)
