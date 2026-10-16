"""
Control-room HTTP server (stdlib only): serves live/static/ and a small JSON API.

  GET  /api/state?h=<last history seq>&e=<last event id>   snapshot + new trend points + new events
  POST /api/action  {"action": "...", ...}                  queue a scenario / operator action

Binds to 127.0.0.1 only: the actions drive a (simulated) plant.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from live.engine import Engine

STATIC = Path(__file__).resolve().parent / "static"
ACTIONS = {"authority", "brain", "level_drift", "pressure_dropout", "forged", "stolen_key",
           "oob_write", "clear_faults", "reset_interlock", "reset_plant"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}


def make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):          # keep the console for the engine
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/api/state":
                q = parse_qs(u.query)
                data = engine.read(int(q.get("h", ["0"])[0]), int(q.get("e", ["0"])[0]))
                return self._send(200, json.dumps(data).encode())
            name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (STATIC / name).resolve()
            if STATIC not in f.parents or not f.is_file():
                return self._send(404, b'{"error":"not found"}')
            self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

        def do_POST(self):
            if urlparse(self.path).path != "/api/action":
                return self._send(404, b'{"error":"not found"}')
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                action = body.pop("action", None)
                if action not in ACTIONS:
                    raise ValueError(f"unknown action {action!r}")
                res = engine.submit(action, **body)
                self._send(200, json.dumps({"ok": True, "result": res}).encode())
            except (ValueError, TypeError) as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}).encode())

    return Handler


def serve(engine: Engine, port: int = 8765) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine))
    srv.daemon_threads = True
    return srv
