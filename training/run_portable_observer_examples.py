"""Replay development smoke inputs through a separately started local GGUF server."""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:11437")
    parser.add_argument("--requests", type=Path, default=Path(__file__).with_name("example-requests.json"))
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Examples use local HTTP inference only")
    for body in json.loads(args.requests.read_text(encoding="utf-8")):
        request = urllib.request.Request(args.url.rstrip("/")+"/v1/chat/completions",
            data=json.dumps(body, allow_nan=False).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
        print(result["choices"][0]["message"]["content"], flush=True)


if __name__ == "__main__":
    main()
