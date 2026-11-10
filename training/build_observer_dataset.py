"""
Build the v3 L2 observer dataset (l2_monitor/observer_v3_format.py).

v3 fixes v2's TELEMETRY side only:
  * PROPOSALS examples are v2's, carried over VERBATIM (user and assistant turns
    byte-identical; only the system prompt is the v3 one, whose proposals
    paragraph is itself v2's verbatim): training/observer_v2/observer_train.jsonl
    -> train, observer_val.jsonl -> val.
  * TELEMETRY examples are regenerated in the v3 format (15 s windows, every
    flow, commanded vs actual valve), labelled by construction from REAL plant
    recordings:
      training/states.jsonl           collect_states.py: normal operation, setpoint
                                      steps, pressure excursions, an SIS trip, native
                                      sensor faults, valve degradation (no commanded
                                      record: in normal operation commanded == actual)
      training/observer_states.jsonl  collect_observer_states.py: 24 native sensor and
                                      actuator faults with L1's commanded valves
    plus sensor/actuator faults SYNTHESIZED onto real normal windows (one channel
    or valve at a time, many modes / severities / onsets) for variety.

Scenario-level holdout -> observer_unseen.jsonl (telemetry only; never trained on):
  * whole rollouts: post_reset_kick, pressure_excursion_partial_feed, and one native
    rollout per fault family (UNSEEN_NATIVE)
  * synthesized sensor faults on purge_flow / a_in_purge (trained on the others) and
    at severities beyond the training range
  * synthesized actuator faults on f2 (trained on f1 / purge / product) and the
    "offset" fault shape (trained on stuck / gain)
val is a random split of the in-distribution pool (as v2's val was).

Labels use the SIS pair only to decide what a window IS; the model input is
measured values and L1's commands only.

Usage: python -m training.build_observer_dataset [--seed 7]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from l2_monitor import observer_v3_format as F

HERE = Path(__file__).resolve().parent
STATES = HERE / "states.jsonl"
OBS_STATES = HERE / "observer_states.jsonl"
V2_DIR = HERE / "observer_v2"
REC = 2 * int(F.SAMPLE_S * (F.WINDOW_N - 1)) + 1     # 2 Hz records spanning one window (31)
TRIP = 3000.0

# telemetry examples per split (proposals come from v2 as-is)
TELEMETRY_COUNTS = {
    "train": {"normal": 600, "excursion": 450, "sensor_fault": 450, "actuator_fault": 450},
    "val": {"normal": 150, "excursion": 120, "sensor_fault": 120, "actuator_fault": 120},
    "unseen": {"normal": 120, "excursion": 120, "sensor_fault": 120, "actuator_fault": 120},
}

NORMAL_ROLLOUTS = {"nominal_expert", "prod_high_130", "prod_low_70", "prod_steps",
                   "pressure_sp_steps", "post_reset_kick"}
EXCURSION_ROLLOUTS = {"pressure_excursion", "pressure_excursion_near_trip",
                      "pressure_excursion_partial_feed", "sis_trip"}
SENSOR_ROLLOUTS = {      # existing native sensor faults: name -> (onset s, faulted channel, mode)
    "pressure_sensor_noise": (0.0, "pressure", "noise"),
    "level_drift_high": (10.0, "level", "drift"),
    "level_frozen_feed_moves": (5.0, "level", "frozen"),
    "sensor_dropouts": (0.0, "pressure", "dropout"),
    "pressure_dropout_heavy": (0.0, "pressure", "dropout"),
}
UNSEEN_ROLLOUTS = {"post_reset_kick", "pressure_excursion_partial_feed"}
UNSEEN_NATIVE = {"tank_level_drift", "f1_flow_frozen", "analyzer_drift", "purge_flow_noise",
                 "product_stuck", "product_cv"}
SIM_CHANNEL = {"tank_pressure": "pressure", "tank_level": "level", "f1_flow": "f1_flow",
               "f2_flow": "f2_flow", "purge_flow": "purge_flow", "product_flow": "product_flow",
               "analyzer": "a_in_purge"}

TRAIN_SENSOR_CH = ("pressure", "level", "f1_flow", "f2_flow", "product_flow")
UNSEEN_SENSOR_CH = ("purge_flow", "a_in_purge")
SENSOR_MODES = ("frozen", "drift", "noise", "dropout", "bias")
TRAIN_VALVES, UNSEEN_VALVES = ("f1", "purge", "product"), ("f2",)
CH_NAME = {"pressure": "Pressure", "level": "Level", "f1_flow": "Feed 1 flow", "f2_flow": "Feed 2 flow",
           "purge_flow": "Purge flow", "product_flow": "Product flow", "a_in_purge": "A-in-purge analyzer"}

Example = Tuple[str, str, str, str]                 # (label, kind, user, assistant)


# ---------------------------------------------------------------- loading
def load_rollouts(path: Path) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = defaultdict(list)
    if path.exists():
        for line in path.open():
            if line.strip():
                r = json.loads(line)
                out[r["rollout"]].append(r)
    return dict(out)


def windows(recs: List[dict], stride: int):
    for i in range(0, len(recs) - REC + 1, stride):
        yield recs[i:i + REC]


def _sub(w):
    """2 Hz records -> WINDOW_N samples at SAMPLE_S spacing."""
    return w[::2]


def _cmd_of(rec) -> dict:
    """L1's commanded valves (recorded), or - where the recording predates that - the
    actual positions, which is what L1 commanded in normal operation."""
    m = rec["measured"]
    return rec.get("commanded") or {v: m[f"{v}_valve_pos"] for v in F.VALVES}


def _slope(xs, dt):
    n, mean = len(xs), sum(xs) / len(xs)
    tm = (n - 1) / 2.0
    return sum((i - tm) * (x - mean) for i, x in enumerate(xs)) / (sum((i - tm) ** 2 for i in range(n)) * dt)


def tel(label: str, score: float, reason: str, meas, cmd, window_s: float) -> Example:
    return label, "telemetry", F.telemetry_to_json(meas, cmd, window_s), F.verdict_to_json(label, score, reason)


def _win(w):
    s = _sub(w)
    return [r["measured"] for r in s], [_cmd_of(r) for r in s], s[-1]["t"] - s[0]["t"]


# ---------------------------------------------------------------- reasons
def reason_normal(meas) -> str:
    p, lv = [m["pressure"] for m in meas], [m["level"] for m in meas]
    return (f"Readings are consistent with normal operation: pressure {p[-1]:.0f} kPa "
            f"(range {max(p) - min(p):.1f}), level {lv[-1]:.1f} % (range {max(lv) - min(lv):.2f}); "
            "valves follow their commands.")


def reason_sensor(ch: str, mode: str, xs: List[float], fault_s: float) -> str:
    name, nd = CH_NAME[ch], (3 if ch == "a_in_purge" else 1)
    if mode == "frozen":
        return f"{name} reading is stuck at {xs[-1]:.{nd}f} for {fault_s:.0f} s while the process keeps moving."
    if mode == "dropout":
        return f"{name} drops to 0 in {sum(x == 0 for x in xs)} of {len(xs)} samples."
    if mode == "noise":
        return f"{name} is noisy: it spans {max(xs) - min(xs):.{nd}f} in the window, wider than the process supports."
    d = xs[-1] - xs[0]
    if mode == "drift":
        return (f"{name} drifts {'up' if d > 0 else 'down'} by {abs(d):.{nd}f} over the window with no "
                "matching change in the related readings.")
    return f"{name} jumps {'up' if d > 0 else 'down'} by {abs(d):.{nd}f} with no matching change in the valves or flows."


def reason_actuator(v: str, cmd: List[float], act: List[float]) -> str:
    gap = max(abs(a - c) for a, c in zip(act, cmd))
    return (f"{v.capitalize()} valve is commanded to {cmd[-1]:.1f}% but sits at {act[-1]:.1f}% "
            f"(gap up to {gap:.1f}%); it is not following the controller.")


def reason_capacity(v: str) -> str:
    return f"{v.capitalize()} valve follows its command but its flow is low for that opening; valve capacity is degraded."


# ---------------------------------------------------------------- telemetry from recordings
def recorded_telemetry(rollouts, native):
    pools = {"seen": defaultdict(list), "unseen": defaultdict(list)}
    base = {"seen": [], "unseen": []}                  # (meas, cmd, window_s) normal windows for synthesis
    for name, recs in rollouts.items():
        split = "unseen" if name in UNSEEN_ROLLOUTS else "seen"
        if name in NORMAL_ROLLOUTS or name in EXCURSION_ROLLOUTS:
            for w in windows(recs, 1):           # 15 s windows: step one record to get enough of them
                meas, cmd, ws = _win(w)
                tp = [r["truth_eval_only"]["pressure"] for r in _sub(w)]
                pmax, slope = max(tp), _slope(tp, F.SAMPLE_S)
                driven = any(r["phase"].endswith("drive") for r in w)
                if (pmax >= 2850.0 and slope > -0.5) or (slope >= 1.5 and tp[-1] >= 2750.0):
                    score = min(1.0, max(0.3, 0.3 + 0.7 * (pmax - 2750.0) / 250.0))
                    mp = [m["pressure"] for m in meas]
                    reason = (f"Pressure is being driven toward the {TRIP:.0f} kPa trip: {max(mp):.0f} kPa, "
                              f"rising {_slope(mp, F.SAMPLE_S):+.1f} kPa/s.")
                    pools[split]["excursion"].append(tel("excursion", score, reason, meas, cmd, ws))
                elif pmax <= 2790.0 and not driven and not any(m["scrammed"] for m in meas):
                    pools[split]["normal"].append(tel("normal", 0.0, reason_normal(meas), meas, cmd, ws))
                    base[split].append((meas, cmd, ws))
        elif name in SENSOR_ROLLOUTS:
            onset, ch, mode = SENSOR_ROLLOUTS[name]
            for w in windows(recs, 2):
                if w[0]["t"] < onset + 3.0:
                    continue
                meas, cmd, ws = _win(w)
                if mode == "drift" and abs(meas[-1]["level"] - w[-1]["truth_eval_only"]["level"]) < 0.3:
                    continue
                if mode == "frozen":
                    tl = [r["truth_eval_only"]["level"] for r in w]
                    if max(tl) - min(tl) < 0.03:
                        continue                       # nothing for a frozen reading to miss
                score = {"frozen": 0.6, "dropout": 0.7, "noise": 0.6, "drift": 0.7}[mode]
                pools[split]["sensor_fault"].append(
                    tel("sensor_fault", score, reason_sensor(ch, mode, [m[ch] for m in meas], ws), meas, cmd, ws))
        elif name == "valve_degradation":
            for w in windows(recs, 3):
                if w[0]["t"] >= 10.0:
                    meas, cmd, ws = _win(w)
                    pools[split]["actuator_fault"].append(tel(
                        "actuator_fault", 0.5, "Product and feed 1 valves follow their commands but their flows "
                        "are low for those openings; valve capacity is degraded.", meas, cmd, ws))
    for name, recs in native.items():                  # native faults, with L1's real commands
        split = "unseen" if name in UNSEEN_NATIVE else "seen"
        kind, ch, mode = recs[0]["kind"], recs[0]["channel"], recs[0]["mode"]
        for w in windows(recs, 2):
            if sum(r["fault_active"] for r in w) < 20:     # >= 10 s of the fault in the window
                continue
            meas, cmd, ws = _win(w)
            fault_s = sum(r["fault_active"] for r in w) * 0.5
            if kind == "sensor_fault":
                c = SIM_CHANNEL[ch]
                if mode == "frozen" and c in ("pressure", "level"):
                    tr = [r["truth_eval_only"][c] for r in w]
                    if max(tr) - min(tr) < (1.0 if c == "pressure" else 0.03):
                        continue
                pools[split]["sensor_fault"].append(
                    tel("sensor_fault", 0.6, reason_sensor(c, mode, [m[c] for m in meas], fault_s), meas, cmd, ws))
            elif mode == "stuck":
                act, cv = [m[f"{ch}_valve_pos"] for m in meas], [c[ch] for c in cmd]
                gap = max(abs(a - c) for a, c in zip(act, cv))
                if gap >= 2.0:
                    pools[split]["actuator_fault"].append(tel(
                        "actuator_fault", min(0.95, 0.4 + 0.015 * gap), reason_actuator(ch, cv, act), meas, cmd, ws))
            else:
                pools[split]["actuator_fault"].append(
                    tel("actuator_fault", 0.5, reason_capacity(ch), meas, cmd, ws))
    return pools, base


# ---------------------------------------------------------------- synthesized faults
def _scale(ch: str, xs: List[float]) -> float:
    return {"pressure": 40.0, "level": 0.8, "a_in_purge": 0.01}.get(ch, 0.08 * max(sum(xs) / len(xs), 1.0))


def synth_sensor(base, ch: str, mode: str, mult: float, rng: random.Random) -> Optional[Example]:
    meas, cmd, ws = base
    xs = [m[ch] for m in meas]
    n, onset = len(xs), rng.randint(0, len(xs) // 2)
    s, sign = _scale(ch, xs), rng.choice((-1.0, 1.0))
    ys = list(xs)
    if mode == "frozen":
        if max(xs[onset:]) - min(xs[onset:]) < 0.25 * s:
            return None                                # a frozen value would be invisible here
        ys[onset:] = [xs[onset]] * (n - onset)
        score = 0.6
    elif mode == "drift":
        total = sign * mult * s
        ys = [x + (total * (i - onset) / max(1, n - 1 - onset) if i >= onset else 0.0) for i, x in enumerate(xs)]
        score = 0.35 + 0.12 * mult
    elif mode == "noise":
        ys = [x + (rng.gauss(0.0, 0.4 * mult * s) if i >= onset else 0.0) for i, x in enumerate(xs)]
        score = 0.35 + 0.12 * mult
    elif mode == "bias":
        ys = [x + (sign * mult * s if i >= onset else 0.0) for i, x in enumerate(xs)]
        score = 0.35 + 0.12 * mult
    else:                                              # dropout reads 0, like the sim's native mode
        frac = min(0.8, 0.1 + 0.1 * mult)
        for i in rng.sample(range(onset, n), max(1, int(frac * (n - onset)))):
            ys[i] = 0.0
        score = 0.5 + 0.5 * frac
    hi = 1.0 if ch == "a_in_purge" else float("inf")
    ys = [min(hi, max(0.0, y)) for y in ys]
    out = [{**m, ch: round(y, 4)} for m, y in zip(meas, ys)]
    return tel("sensor_fault", min(0.95, score), reason_sensor(ch, mode, ys, (n - onset) * F.SAMPLE_S), out, cmd, ws)


def synth_actuator(base, v: str, shape: str, rng: random.Random) -> Optional[Example]:
    meas, cmd0, ws = base
    act = [m[f"{v}_valve_pos"] for m in meas]
    n, onset = len(act), rng.randint(0, len(act) // 2)
    if shape == "stuck":                               # a still valve whose command walks away
        if max(act) - min(act) > 0.5:
            return None
        delta = rng.uniform(5.0, 30.0) * (1.0 if act[0] < 50.0 else -1.0)
        ramp = rng.randint(1, 5)
        cmd = [a + (delta * min(1.0, (i - onset + 1) / ramp) if i >= onset else 0.0) for i, a in enumerate(act)]
    elif shape == "gain":                              # opens only k x what it is told
        if sum(act) / n < 5.0:
            return None
        k = rng.uniform(0.5, 0.8)
        cmd = [min(100.0, a / k) if i >= onset else a for i, a in enumerate(act)]
    else:                                              # "offset" (unseen shape)
        c = rng.uniform(5.0, 20.0) * (1.0 if act[0] < 50.0 else -1.0)
        cmd = [min(100.0, max(0.0, a + c)) if i >= onset else a for i, a in enumerate(act)]
    gap = max(abs(a - c) for a, c in zip(act, cmd))
    if gap < 2.0:
        return None
    full = [{**c0, v: round(c, 4)} for c0, c in zip(cmd0, cmd)]
    return tel("actuator_fault", min(0.95, 0.4 + 0.015 * gap), reason_actuator(v, cmd, act), meas, full, ws)


def synthesized_telemetry(base, rng: random.Random, n_seen: int, n_unseen: int):
    pools = {"seen": defaultdict(list), "unseen": defaultdict(list)}
    plan = {"seen": (TRAIN_SENSOR_CH, (1.0, 3.0), TRAIN_VALVES, ("stuck", "gain"), n_seen),
            "unseen": (UNSEEN_SENSOR_CH, (4.0, 6.0), UNSEEN_VALVES + TRAIN_VALVES, ("offset",), n_unseen)}
    for split, (chans, mults, valves, shapes, n) in plan.items():
        src = base[split] or base["seen"]
        for _ in range(n * 6):
            if len(pools[split]["sensor_fault"]) >= n:
                break
            ex = synth_sensor(rng.choice(src), rng.choice(chans), rng.choice(SENSOR_MODES), rng.uniform(*mults), rng)
            if ex:
                pools[split]["sensor_fault"].append(ex)
        for _ in range(n * 10):
            if len(pools[split]["actuator_fault"]) >= n:
                break
            v = rng.choice(valves)
            # unseen: f2 with any shape, the trained valves only with the held-out shape
            shape = rng.choice(("stuck", "gain", "offset")) if v in UNSEEN_VALVES else rng.choice(shapes)
            ex = synth_actuator(rng.choice(src), v, shape, rng)
            if ex:
                pools[split]["actuator_fault"].append(ex)
    return pools


# ---------------------------------------------------------------- v2 proposals, verbatim
def v2_proposals(split: str, v2_dir: Path = V2_DIR) -> List[Example]:
    out = []
    for line in (v2_dir / f"observer_{split}.jsonl").open(encoding="utf-8"):
        m = json.loads(line)["messages"]
        if json.loads(m[1]["content"])["input"] == "proposals":
            out.append((json.loads(m[2]["content"])["label"], "proposals", m[1]["content"], m[2]["content"]))
    return out


# ---------------------------------------------------------------- assemble
def build(seed: int = 7, out_dir: Path = HERE, counts: dict = TELEMETRY_COUNTS, states: Path = STATES,
          obs_states: Path = OBS_STATES, v2_dir: Path = V2_DIR) -> dict:
    rng = random.Random(seed)
    tel_pools, base = recorded_telemetry(load_rollouts(states), load_rollouts(obs_states))
    need_seen = max(counts["train"].values()) + max(counts["val"].values())
    syn = synthesized_telemetry(base, rng, need_seen, max(counts["unseen"].values()))
    pools = {s: defaultdict(list) for s in ("seen", "unseen")}
    for src in (tel_pools, syn):
        for s in pools:
            for label, exs in src[s].items():
                pools[s][label].extend(exs)

    splits = {"train": v2_proposals("train", v2_dir), "val": v2_proposals("val", v2_dir), "unseen": []}
    short = {}
    for label in ("normal", "excursion", "sensor_fault", "actuator_fault"):
        seen = list(pools["seen"][label])
        rng.shuffle(seen)
        nv, nt = counts["val"][label], counts["train"][label]
        val, rest = seen[:nv], seen[nv:]
        if len(rest) < nt:                             # short pool: resample (reported)
            short[label] = len(rest)
            rest = [rest[i % len(rest)] for i in range(nt)]
        splits["val"] += val
        splits["train"] += rest[:nt]
        uns = list(pools["unseen"][label])
        rng.shuffle(uns)
        splits["unseen"] += uns[:counts["unseen"][label]]
    train_users = {e[2] for e in splits["train"]}
    splits["unseen"] = [e for e in splits["unseen"] if e[2] not in train_users]
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    for name, exs in splits.items():
        rng.shuffle(exs)
        with (out_dir / f"observer_v3_{name}.jsonl").open("w", encoding="utf-8") as f:
            for label, kind, user, asst in exs:
                f.write(json.dumps({"messages": F.messages(user, asst)}) + "\n")
        report[name] = {"n": len(exs), "by_label": dict(Counter(e[0] for e in exs)),
                        "by_input_label": {f"{k}/{lab}": n for (k, lab), n in
                                           sorted(Counter((e[1], e[0]) for e in exs).items())}}
    report["telemetry_train_pool_short"] = short
    report["_samples"] = [e for e in splits["train"] if e[1] == "telemetry"][:4]
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    r = build(a.seed)
    samples = r.pop("_samples")
    print(json.dumps(r, indent=1))
    for label, _, user, asst in samples:
        print(f"\n[{label}] user ({len(user)} chars): {user[:500]}...\n  assistant: {asst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
