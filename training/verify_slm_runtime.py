"""Real Ollama candidate integration on an offline replay; never controls a plant."""
from __future__ import annotations

import json
import time
import urllib.request

from contracts import ControlCommand, PlantState, SupervisoryTargets
from monitor.observer import _http_post_json
from monitor.observer_runtime import Request, UnifiedObserver, make_observer
from monitor.telemetry_classifier import approved_specialist
from training.shared_slm_native import write
from training.slm_artifact_tools import DATA, LLAMA_SERVER, OLLAMA_URL, ROOT, RUN


def main():
    if LLAMA_SERVER:
        from monitor.llama_cpp_transport import llama_cpp_post
        from training.local_gguf_server import ensure, residency
        ensure(RUN, "scram-observer-slm-candidate")
    real_post = llama_cpp_post if LLAMA_SERVER else _http_post_json
    data = [json.loads(line) for line in (DATA / "val.jsonl").read_text(encoding="utf-8").splitlines()]
    proposal = next(row for row in data if row["kind"] == "proposals")
    history = proposal["input"]["proposals"]
    replay = ROOT / "artifacts/optimization/runtime-replay.jsonl"
    requests = []
    def post(url, body, timeout):
        if body["model"] != "scram-observer-slm-candidate":
            raise AssertionError("unexpected model in unified integration")
        if body["options"]["num_ctx"] != 3072:
            raise AssertionError("context mismatch")
        requests.append({"model": body["model"], "options": body["options"],
                         "task": json.loads(body["messages"][1]["content"])["task"],
                         "message_count": len(body["messages"]), "format": body["format"]})
        return real_post(url, body, timeout)
    import os
    previous = {key: os.environ.get(key) for key in ("SCRAM_OBSERVER_MODEL", "SCRAM_OBSERVER_SCHEMA", "SCRAM_OBSERVER_URL", "SCRAM_OBSERVER_BACKEND")}
    os.environ.update(SCRAM_OBSERVER_MODEL="scram-observer-slm-candidate",
                      SCRAM_OBSERVER_SCHEMA="slm-v1", SCRAM_OBSERVER_URL=OLLAMA_URL)
    os.environ["SCRAM_OBSERVER_BACKEND"] = "llama_cpp" if LLAMA_SERVER else "ollama"
    observer = make_observer("unified", telemetry_specialist=approved_specialist(), http_post=post)
    try:
        for line in replay.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            p = history[min(int(row["t"]//5), len(history)-1)]
            intent = SupervisoryTargets(**{key: p[key] for key in
                ("pressure_sp", "level_sp", "production_rate_sp", "a_comp_sp", "rationale")})
            observer.feed(row["t"], PlantState(**row["state"]), intent, ControlCommand(**row["commanded"]))
            if not observer.wait_idle(30):
                raise RuntimeError("real candidate inference did not become idle")
        snapshot = observer.snapshot()
        if not {"proposal_review", "telemetry_review"} <= {r["task"] for r in requests}:
            raise AssertionError("both task modes were not invoked")
        if LLAMA_SERVER:
            resident = residency(RUN)
            if len(resident["models"]["data"]) != 1 or resident["process"]["alias"] != "scram-observer-slm-candidate":
                raise AssertionError("expected exactly one resident candidate")
        else:
            with urllib.request.urlopen(OLLAMA_URL+"/api/ps", timeout=5) as response:
                resident = json.load(response)
            if len(resident.get("models", [])) != 1 or not resident["models"][0]["name"].startswith("scram-observer-slm-candidate"):
                raise AssertionError("expected exactly one resident candidate")
        # Force a lifecycle change during a genuine inference call. The response
        # is real, but must be rejected after reset even if its JSON is valid.
        late = None
        def resetting_post(url, body, timeout):
            late.reset()
            return real_post(url, body, timeout)
        late = UnifiedObserver(model="scram-observer-slm-candidate", base_url=OLLAMA_URL,
                               include_identity=False, http_post=resetting_post)
        late.paths["proposals"].sequence = 1
        now = time.monotonic()
        late._execute(Request("proposals", json.dumps(proposal["input"]), 0, 1, 0, now, now+20, late.run_id))
        if late.discarded != 1 or late.last:
            raise AssertionError("late real inference result survived reset")
        write(RUN / "runtime-integration.json", {"real_inference": True, "plant_writes": False,
              "replay": str(replay), "is_quality_benchmark": False, "requests": requests,
              "observer": snapshot, "residency": resident, "reset_race": late.snapshot(),
              "backend": "Ollama-bundled llama.cpp / CUDA v12" if LLAMA_SERVER else "Ollama / CUDA"})
    finally:
        observer.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()
