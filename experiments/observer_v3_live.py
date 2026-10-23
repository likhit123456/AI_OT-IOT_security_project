"""Phase 4b live sanity set (python experiments/observer_v3_live.py), in-process on live.engine.Engine
Audits every telemetry prompt against the v3 parity gate and every observer verdict handed
to governance. Writes JSON results to argv[1]."""
import json, sys, time
from collections import Counter
sys.path[:0] = [".", "tests"]
from test_observer_v3_format import rebuild, telemetry_key_order
from live.engine import Engine

OUT = sys.argv[1] if len(sys.argv) > 1 else "experiments/observer_v3_live_results.json"
KEYS = telemetry_key_order()
eng = Engine(brain="ollama").start()
res = {"phases": {}}
gov_args = []                  # every observer verdict / suspicion governance received
audit = {"telemetry_inputs": 0, "parity_ok": 0, "key_order_ok": 0, "window_s": [], "bad": []}
seen_tel_user, seen_ev = set(), set()


def wrap_gov():
    g = eng.governor
    orig = g.update
    if getattr(orig, "_wrapped", False):
        return
    def upd(t, rule_score, rule_source, obs_verdict=None, obs_suspicion=0.0, intent=None):
        gov_args.append((t, None if obs_verdict is None else obs_verdict.kind, obs_suspicion,
                         eng.observer.last.get("telemetry").label if eng.observer and eng.observer.last.get("telemetry") else None))
        return orig(t, rule_score, rule_source, obs_verdict, obs_suspicion, intent)
    upd._wrapped = True
    g.update = upd


def p(*a):
    print(*a, flush=True)


def tick(phase, rec):
    wrap_gov()
    o = eng.observer
    if o is not None:
        u = (o.paths["telemetry"].last_user if o.paths["telemetry"] else {}).get("telemetry")
        if u and u not in seen_tel_user:
            seen_tel_user.add(u)
            audit["telemetry_inputs"] += 1
            ok = rebuild(u) == u
            d = json.loads(u)
            ko = list(d) == KEYS
            audit["parity_ok"] += ok; audit["key_order_ok"] += ko; audit["window_s"].append(d["window_s"])
            if not (ok and ko) and len(audit["bad"]) < 3:
                audit["bad"].append(u)
            if "sample_input" not in res:
                res["sample_input"] = d
    for e in list(eng.events):
        if e["id"] in seen_ev:
            continue
        seen_ev.add(e["id"])
        if e["kind"] in ("attack", "governance", "observer", "monitor", "sis", "error", "plant"):
            p(f"  [{e['t']:7.1f}] {e['kind']}: {e['msg'][:170]}")
            rec["events"].append(e)
    g = eng.governor.last
    if g is not None:
        rec["applied_min"] = min(rec.get("applied_min", 1.0), g.applied)
        if not rec["gov"] or g.t - rec["gov"][-1]["t"] >= 1.0:
            rec["gov"].append(g.as_dict())
    if o is not None:
        for kind in ("telemetry", "proposals"):
            v = o.last.get(kind)
            if v is not None and (kind, v.t) not in rec["_vseen"]:
                rec["_vseen"].add((kind, v.t))
                rec[f"{kind}_labels"][str(v.label)] += 1
                rec[f"{kind}_verdicts"].append({"t": round(v.t, 1), "label": v.label, "score": v.score,
                                                "reason": v.reason[:160], "latency_s": round(v.latency_s, 2)})


def phase(name, seconds, until=None):
    rec = {"t0": round(eng.now(), 1), "events": [], "gov": [], "_vseen": set(),
           "telemetry_labels": Counter(), "proposals_labels": Counter(),
           "telemetry_verdicts": [], "proposals_verdicts": []}
    p(f"=== {name} (t={eng.now():.1f})")
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        tick(name, rec)
        if until and until(rec):
            break
        time.sleep(0.25)
    rec["t1"] = round(eng.now(), 1)
    rec.pop("_vseen")
    res["phases"][name] = rec
    g = eng.governor.last
    p(f"--- {name}: applied_min={rec.get('applied_min')} now={g.applied if g else None} "
      f"telemetry={dict(rec['telemetry_labels'])} proposals={dict(rec['proposals_labels'])}")
    save()
    return rec


def save():
    tel_with_alarm = [a for a in gov_args if a[3] not in (None, "normal")]
    res["routing"] = {
        "governor_updates": len(gov_args),
        "obs_verdict_kinds_passed": dict(Counter(str(a[1]) for a in gov_args)),
        "updates_while_telemetry_alarm": len(tel_with_alarm),
    }
    res["audit"] = {**audit, "window_s_range": [min(audit["window_s"]), max(audit["window_s"])] if audit["window_s"] else None}
    res["audit"].pop("window_s")
    res["observer_model"] = eng.observer.snapshot()["models"] if eng.observer else None
    with open(OUT, "w") as f:
        json.dump(res, f, indent=1, default=lambda x: dict(x) if isinstance(x, Counter) else str(x))


# wait for the engine to come up
while eng.status != "running" or eng.cycles < 5:
    if eng.status == "error":
        p("ENGINE ERROR", eng.error); sys.exit(1)
    time.sleep(0.5)
p("engine running; observer =", eng.observer.snapshot()["models"] if eng.observer else None, "brain =", eng.brain_label())

phase("clean", 240)
eng.submit("operator_injection", duration_s=90.0)
inj = phase("injection", 95)
phase("recovery", 300, until=lambda r: eng.governor.last and eng.governor.last.applied >= 1.0
      and eng.now() - inj["t1"] > 20)
eng.submit("actuator_fault", valve="product", how="cv")
phase("actuator_product_cv", 120)
eng.submit("clear_faults")
phase("after_cv_clear", 60)
eng.submit("actuator_fault", valve="product", how="stuck")
phase("actuator_product_stuck", 120)
eng.submit("clear_faults")
phase("after_stuck_clear", 45)
save()
p("SANITY DONE")
eng.stop()
