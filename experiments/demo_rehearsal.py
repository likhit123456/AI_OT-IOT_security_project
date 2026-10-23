"""Dry run of DEMO_RUNBOOK.md on the live engine (same Engine the control room drives),
with the same actions in the same order, recording what the audience would see at each step.

    python experiments/demo_rehearsal.py [--from N] [--out FILE]   (default out: experiments/demo_rehearsal_results.json)

Steps (DEMO_RUNBOOK.md): 1 clean -> 2 forged commands -> 3 operator injection -> 4 product
valve fault -> 5 level-sensor drift (Keel catches the measured/safety-channel divergence) ->
6 stolen key (SIS trips on the true pressure) -> re-arm. --from N starts at step N.
"""
import argparse
import json
import sys
import time

sys.path[:0] = ["."]
from live.engine import Engine

ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="start", type=int, default=1)
ap.add_argument("--out", default="experiments/demo_rehearsal_results.json")
args = ap.parse_args()
OUT = args.out
eng = Engine(brain="ollama").start()
res = {"steps": []}
seen = set()


def p(*a):
    print(*a, flush=True)


def snap():
    s = eng._snapshot()
    g = s["governance"] or {}
    return {"t": s["t"], "authority_brain": round(min(eng.authority, s["keel"]["authority"], g.get("applied", 1.0)), 3),
            "keel_A": round(s["keel"]["authority"], 3), "gov_applied": g.get("applied"),
            "P_meas": s["measured"]["pressure"], "P_true": s["truth"]["pressure"],
            "L_meas": s["measured"]["level"], "L_true": s["truth"]["level"],
            "gate": dict(s["gate"]), "sis": s["sis"]["tripped"], "sis_reason": s["sis"]["reason"],
            "can_reset": s["sis"]["can_reset"]}


def step(name, seconds, until=None, every=1.0):
    rec = {"name": name, "t0": round(eng.now(), 1), "trace": [], "events": []}
    p(f"=== {name} (t={eng.now():.1f})")
    end, last = time.monotonic() + seconds, -1e9
    while time.monotonic() < end:
        for e in list(eng.events):
            if e["id"] not in seen:
                seen.add(e["id"])
                if e["kind"] in ("attack", "gate", "governance", "observer", "sis", "keel", "error", "plant"):
                    rec["events"].append(e)
                    p(f"  [{e['t']:7.1f}] {e['kind']}: {e['msg'][:160]}")
        if time.monotonic() - last >= every:
            last = time.monotonic()
            rec["trace"].append(snap())
        if until and until(rec):
            break
        time.sleep(0.2)
    rec["t1"] = round(eng.now(), 1)
    tr = rec["trace"]
    rec["summary"] = {"authority_min": min(x["authority_brain"] for x in tr), "keel_A_min": min(x["keel_A"] for x in tr),
                      "gov_min": min((x["gov_applied"] for x in tr if x["gov_applied"] is not None), default=None),
                      "P_true_range": [min(x["P_true"] for x in tr), max(x["P_true"] for x in tr)],
                      "L_true_min": min(x["L_true"] for x in tr), "gate_end": tr[-1]["gate"], "end": tr[-1]}
    p(f"--- {name}: {json.dumps(rec['summary'])}")
    res["steps"].append(rec)
    json.dump(res, open(OUT, "w"), indent=1)
    return rec


while eng.status != "running" or eng.cycles < 5:
    if eng.status == "error":
        p("ENGINE ERROR", eng.error); sys.exit(1)
    time.sleep(0.5)
full = lambda r: r["trace"] and r["trace"][-1]["authority_brain"] >= 0.999

diverted = lambda r: r["trace"] and r["trace"][-1]["keel_A"] <= 0.0 and eng.now() - r["t0"] > 5
tripped = lambda r: r["trace"] and r["trace"][-1]["sis"] and eng.now() - r["t0"] > 2
rearmable = lambda r: r["trace"] and r["trace"][-1]["can_reset"]
STEPS = {
    1: lambda: step("1 clean", 60),
    2: lambda: (eng.submit("forged", duration_s=60.0), step("2 forged commands", 62),
                step("2b recover", 120, until=lambda r: full(r) and eng.now() - r["t0"] > 10)),
    3: lambda: (eng.submit("operator_injection", duration_s=60.0), step("3 operator injection", 62),
                step("3b recover", 180, until=lambda r: full(r) and eng.now() - r["t0"] > 20)),
    4: lambda: (eng.submit("actuator_fault"), step("4 product valve fault", 75),
                eng.submit("clear_faults"), step("4b clear", 40)),
    5: lambda: (eng.submit("level_drift"), step("5 level drift -> Keel divert", 480, until=diverted),
                eng.submit("clear_faults"), step("5b clear", 20)),
    6: lambda: (eng.submit("stolen_key"), step("6 stolen key -> SIS trip", 240, until=tripped),
                step("6b wait for re-arm band", 300, until=rearmable),
                eng.submit("reset_interlock"), step("6c re-armed", 60)),
}
for n in range(args.start, 7):
    STEPS[n]()
p("REHEARSAL DONE")
eng.stop()
