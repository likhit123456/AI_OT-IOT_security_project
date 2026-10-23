"""
Tune the SIS closed-loop safe-hold on the live simulator (the control room must be stopped).

For each candidate gain set: reset the plant, drive it with the stolen-key lineup until the
safety channel reads >= 3000 kPa, then run the safe-hold law from l0_plant/safety.py for
`--hold` seconds and report whether / when the plant settles inside the re-arm band.

    python -m experiments.safe_hold_tune [--hold 150]
"""
from __future__ import annotations

import argparse
import json
import time

from l0_plant.grfics_client import GrficsClient
from l0_plant.safety import SafeHold, SafetyInterlock, TripLimits
from scripts.reset_plant import reset_and_settle

LIM = TripLimits(level_low=40.0, level_low_rearm=42.0)   # live DEMO_LIMITS
CANDIDATES = [SafeHold(), SafeHold(feed_frac=0.5), SafeHold(kp_purge=0.6, kl_product=4.0)]


def truth(raw):
    o = raw.get("outputs", {})
    return float(o.get("pressure", 0.0)), float(o.get("liquid_level", 0.0))


def run(hold: SafeHold, hold_s: float) -> dict:
    reset_and_settle()                           # restarts the container: connect afterwards
    time.sleep(1.0)
    with GrficsClient("127.0.0.1", 55555) as c:
        return _run(c, hold, hold_s)


def _run(c: GrficsClient, hold: SafeHold, hold_s: float) -> dict:
    t0 = time.monotonic()
    while True:                                  # stolen-key takeover
        p, lv = truth(c.write_inputs({"f1_valve_sp": 100.0, "f2_valve_sp": 100.0,
                                      "purge_valve_sp": 0.0, "product_valve_sp": 0.0, "e_stop": 0}))
        if p >= LIM.pressure_high or time.monotonic() - t0 > 240:
            break
        time.sleep(0.5)
    sis = SafetyInterlock(LIM, safe_hold=hold)
    sis.evaluate(p, lv)
    trace, in_band_since, settled_at = [], None, None
    t1 = time.monotonic()
    while time.monotonic() - t1 < hold_s:
        p, lv = truth(c.write_inputs(sis.safe_state_inputs(p, lv)))
        el = time.monotonic() - t1
        trace.append((round(el, 1), round(p, 1), round(lv, 2)))
        if sis.can_reset(p, lv):
            in_band_since = el if in_band_since is None else in_band_since
            if settled_at is None and el - in_band_since >= 10.0:
                settled_at = round(el, 1)
        else:
            in_band_since = None
        time.sleep(0.5)
    ps, ls = [x[1] for x in trace], [x[2] for x in trace]
    return {"hold": hold.__dict__, "takeover_s": round(t1 - t0, 1), "settled_at_s": settled_at,
            "p_min": min(ps), "p_max": max(ps), "l_min": min(ls), "l_max": max(ls),
            "end": trace[-1], "trace": trace[::10], "escalated": sis.escalated}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=150.0)
    a = ap.parse_args()
    out = []
    for h in CANDIDATES:
        r = run(h, a.hold)
        out.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "trace"}), flush=True)
        print("  trace:", r["trace"], flush=True)
    json.dump(out, open("experiments/safe_hold_tune_results.json", "w"), indent=1)
    reset_and_settle()


if __name__ == "__main__":
    main()
