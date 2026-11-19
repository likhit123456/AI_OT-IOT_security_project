"""Isolated review probes: no simulator/model requests and no plant writes."""
import importlib.abc
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live.commands import parse
from live.engine import Engine
from live.server import make_handler


def post_fixture(engine, payload, headers=None):
    handler_type = make_handler(engine)
    handler = handler_type.__new__(handler_type)
    body = json.dumps(payload).encode()
    handler.path = "/api/action"
    handler.headers = {"Content-Length": str(len(body)), **(headers or {})}
    handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
    response = {}
    handler.send_response = lambda code: response.update(status=code)
    handler.send_header = lambda *_: None
    handler.end_headers = lambda: None
    handler.do_POST()
    response["body"] = json.loads(handler.wfile.getvalue())
    return response


def main():
    cases = ["do not open feed 1", "do not release the valves", "do not raise pressure by 50",
             "set pressure to 2600 and level to 50", "feed 1 and feed 2 to 50%"]
    results = {"plant_writes": False, "model_requests": False,
               "command_parser": [{"input": text, "result": parse(text)} for text in cases]}
    calls = []
    fixture = SimpleNamespace(read=lambda *_: {"run_id": "fixture", "telemetry_stale": False},
                              submit=lambda action, **kwargs: calls.append(action) or "queued")
    results["foreign_origin_text_plain"] = post_fixture(
        fixture, {"action": "clear_faults"},
        {"Origin": "https://unrelated.invalid", "Content-Type": "text/plain", "Sec-Fetch-Site": "cross-site"})
    results["foreign_origin_text_plain"]["submitted_to_fixture"] = calls

    engine = Engine(reset=False)
    engine.run_id = "old-run"
    executed = []
    engine._act_forged = lambda: executed.append(engine.run_id)

    def read_then_reset(*_):
        snapshot = {"run_id": engine.run_id, "telemetry_stale": False}
        # Deterministic simulation of a control-thread reset immediately after
        # the HTTP thread's snapshot read, before Engine.submit captures identity.
        engine.run_id = "new-run"
        return snapshot

    engine.read = read_then_reset
    results["reset_between_read_and_submit"] = post_fixture(
        engine, {"action": "forged", "expected_run_id": "old-run"})
    while not engine._q.empty():
        engine._q.get_nowait()()
    results["reset_between_read_and_submit"]["executed_in_runs"] = executed

    code = """import importlib.abc, sys
class AbsentPsutil(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'psutil':
            raise ModuleNotFoundError("No module named 'psutil' (isolated dependency probe)")
sys.meta_path.insert(0, AbsentPsutil())
import training.launch_demo_control_room
"""
    process = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True, timeout=20)
    results["missing_undeclared_psutil"] = {"exit_code": process.returncode, "error": process.stderr.splitlines()[-1],
        "requirements_declare_psutil": any("psutil" in path.read_text().lower() for path in ROOT.glob("*requirements*.txt"))}
    output = ROOT / "artifacts/project-evaluation-probes-20261118.json"
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
