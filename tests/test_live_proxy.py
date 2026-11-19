"""Interrupted Unity downloads never escape the proxy or become cached assets."""
import http.client
import io

import pytest

from live.server import make_handler


def proxy_handler():
    handler = make_handler(None).__new__(make_handler(None))
    handler.wfile = io.BytesIO()
    response = {"headers": {}}
    handler.send_response = lambda code: response.update(status=code)
    handler.send_header = lambda key, value: response["headers"].update({key: value})
    handler.end_headers = lambda: None
    return handler, response


@pytest.mark.parametrize("recover", [True, False])
def test_interrupted_asset_download(monkeypatch, recover):
    calls = []

    class Upstream:
        status = 200
        headers = {"Content-Type": "application/octet-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            if len(calls) == 1 or not recover:
                raise http.client.IncompleteRead(b"partial", 100)
            return b"complete asset"

    def open_asset(url, timeout):
        calls.append((url, timeout))
        return Upstream()

    monkeypatch.setattr("live.server.urllib.request.urlopen", open_asset)
    handler, response = proxy_handler()
    handler._proxy("/Build/plant.unityweb", "v=1")
    assert len(calls) == 2
    assert calls[0][0].endswith("/Build/plant.unityweb?v=1")
    assert response["status"] == (200 if recover else 502)
    assert b"partial" not in handler.wfile.getvalue()
    if recover:
        assert handler.wfile.getvalue() == b"complete asset"
        assert response["headers"]["Content-Length"] == "14"
    else:
        assert response["headers"]["Cache-Control"] == "no-store"
