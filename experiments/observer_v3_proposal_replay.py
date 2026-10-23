"""Capture live proposal inputs (clean, then product cv fault) and replay each through v3 and v2.
import json, sys, time, urllib.request
sys.path[:0] = ["."]
from live.engine import Engine
from monitor import observer_v2_format as V2, observer_v3_format as V3

eng = Engine(brain="ollama").start()
while eng.status != "running" or eng.cycles < 5:
    time.sleep(0.5)
caps, seen = [], set()


def grab(phase, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        u = eng.observer.last_user.get("proposals") if eng.observer else None
        if u and u not in seen:
            seen.add(u)
            caps.append({"phase": phase, "t": round(eng.now(), 1), "user": u})
        time.sleep(0.25)


grab("clean", 90)
eng.submit("actuator_fault", valve="product", how="cv")
grab("product_cv", 150)
eng.submit("clear_faults")
eng.stop()


def ask(model, fmt, user, num_ctx=None):
    opts = {"temperature": 0, "num_predict": 120}
    if num_ctx:
        opts["num_ctx"] = num_ctx
    body = {"model": model, "stream": False, "format": "json", "messages": fmt.messages(user), "options": opts}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    v, _ = V2.parse(json.loads(urllib.request.urlopen(req, timeout=120).read())["message"]["content"])
    return v["label"] if v else None


for c in caps:
    c["v3"] = ask("scram-observer-v3", V3, c["user"], 3072)
    c["v2"] = ask("scram-observer", V2, c["user"])
    last = json.loads(c["user"])["proposals"][-1]
    print(c["phase"], c["t"], "v3:", c["v3"], "v2:", c["v2"], "| last:",
          {k: last[k] for k in V2.SETPOINT_KEYS}, "|", last["rationale"][:90], flush=True)
json.dump(caps, open(sys.argv[1] if len(sys.argv) > 1 else "experiments/observer_v3_proposal_replay.json", "w"), indent=1)
print("CAPTURE DONE")
