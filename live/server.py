"""
Control-room HTTP server (stdlib only): serves live/static/ and a small JSON API.

  GET  /api/state?h=<last history seq>&e=<last event id>   snapshot + new trend points + new events
  POST /api/action  {"action": "...", ...}                  queue a scenario / operator action
  GET  /grfics/reactor.html                                 the GRFICS 3D reactor alone, full-bleed
  GET  /grfics/<path> (and /Build /TemplateData /data)     proxied to the plant's web server (:80), so
                                                           the Unity build loads same-origin, without
                                                           the GRFICS lab dashboard around it

Binds to 127.0.0.1 only: the actions drive a (simulated) plant.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from live.engine import Engine

STATIC = Path(__file__).resolve().parent / "static"
ACTIONS = {"authority", "brain", "level_drift", "pressure_dropout", "forged", "operator_injection", "stolen_key",
           "oob_write", "actuator_fault", "sensor_fault", "chat", "clear_note", "clear_faults",
           "reset_interlock", "reset_plant"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
         ".svg": "image/svg+xml"}
PLANT_WEB = "http://127.0.0.1:80"
PROXY_ROOTS = ("/Build/", "/TemplateData/", "/data/")   # what the Unity page asks for


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

        def _proxy(self, path: str, query: str) -> None:
            url = PLANT_WEB + path + (f"?{query}" if query else "")
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    body = r.read()
                    ctype = r.headers.get("Content-Type", "application/octet-stream")
                    self.send_response(r.status)
            except urllib.error.HTTPError as e:
                return self._send(e.code, b"", "text/plain")
            except (urllib.error.URLError, OSError):
                return self._send(502, b'{"error":"plant web server (:80) not reachable"}')
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if "/Build/" in path else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/grfics/reactor.html":
                return self._send(200, (STATIC / "reactor.html").read_bytes(), TYPES[".html"])
            if u.path.startswith("/grfics/"):
                return self._proxy(u.path[len("/grfics"):], u.query)
            if u.path.startswith(PROXY_ROOTS):
                return self._proxy(u.path, u.query)
            if u.path == "/api/state":
                q = parse_qs(u.query)
                try:
                    since_h, since_e = int(q.get("h", ["0"])[0]), int(q.get("e", ["0"])[0])
                except ValueError:
                    return self._send(400, b'{"error":"h and e must be integers"}')
                data = engine.read(since_h, since_e)
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
                if not isinstance(body, dict):
                    raise ValueError("request body must be a JSON object")
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
