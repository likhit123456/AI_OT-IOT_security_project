"""Import/verify only the frozen experimental alias using ordinary local Ollama.

Official protocol: https://github.com/ollama/ollama/blob/main/docs/api.md
Streams the local GGUF to localhost; never publishes weights, modifies legacy
aliases, edits ACLs, or changes server settings. An import response alone is not
success: show must establish the weight identity and both roles must generate.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from monitor.observer_runtime import parse_verdict

ALIAS = "scram-observer-slm-candidate"


class LocalOllama:
    def __init__(self, url):
        parts = urlsplit(url)
        if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost") or parts.path:
            raise ValueError("verification permits only a local HTTP Ollama server")
        self.host, self.port = parts.hostname, parts.port or 11434

    def request(self, method, path, data=None, file=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=180)
        try:
            if file is not None:
                with file.open("rb") as stream:
                    connection.request(method, path, body=stream, headers={"Content-Length": str(file.stat().st_size),
                        "Content-Type": "application/octet-stream"})
            else:
                body = json.dumps(data).encode() if data is not None else None
                connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read().decode("utf-8", errors="replace")
            return {"status": response.status, "body": json.loads(raw) if raw else None}
        finally:
            connection.close()

    def json(self, method, path, data=None):
        result = self.request(method, path, data)
        if result["status"] != 200 or isinstance(result["body"], dict) and result["body"].get("error"):
            raise RuntimeError(f"{path}: {result}")
        return result["body"]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(directory, output, url, install=False, alias=ALIAS, data=None, resume=False):
    manifest = json.loads((directory / "deployment.json").read_text(encoding="utf-8"))
    if alias not in (ALIAS, "scram-observer-evidence-v2", "scram-observer-demo", "scram-observer-demo-f16"):
        raise ValueError("only explicitly named experimental aliases are allowed")
    if manifest["alias"] != alias or Path(manifest["weights_file"]).name != manifest["weights_file"]:
        raise ValueError("unexpected candidate alias or weight path")
    weights = directory / manifest["weights_file"]
    if digest(weights) != manifest["weights_sha256"]:
        raise ValueError("candidate weight identity mismatch")
    if digest(directory / "Modelfile") != manifest["modelfile_sha256"]:
        raise ValueError("candidate Modelfile identity mismatch")
    if alias in ("scram-observer-evidence-v2", "scram-observer-demo", "scram-observer-demo-f16"):
        if data is None:
            raise ValueError("new evidence-v2 verification requires its own validation data")
        data_manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
        if (data_manifest.get("wire_schema") != "measured-evidence-v2" or
            digest(data / "val.jsonl") != data_manifest["splits"]["val"]["sha256"]):
            raise ValueError("new verification data representation/identity mismatch")
    previous_report = None
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if (not resume or previous.get("status") != "blocked" or previous.get("alias") != alias or
            previous.get("weights_sha256") != manifest["weights_sha256"]):
            raise FileExistsError("existing verification is complete or has another identity")
        previous_report = output.with_name(f"{output.stem}.failed-{uuid.uuid4().hex}.json")
        output.rename(previous_report)
    client = LocalOllama(url)
    result = {"url": url, "alias": alias, "weights_sha256": manifest["weights_sha256"],
              "status": "started", "plant_writes": False, "quality_qualification": False, "stages": []}
    if previous_report:
        result["previous_failed_report"] = str(previous_report)
    candidate_used = False
    before = None
    try:
        result["version"] = client.json("GET", "/api/version")
        before = client.json("GET", "/api/tags")
        result["tags_before"] = before
        resident = client.json("GET", "/api/ps")
        if any(m["name"] != alias+":latest" for m in resident.get("models", [])):
            raise RuntimeError("another user's model is resident; leaving it alone")
        present = any(m["name"] == alias+":latest" for m in before.get("models", []))
        if not present and install:
            blob = "/api/blobs/sha256:"+manifest["weights_sha256"]
            head = client.request("HEAD", blob)
            result["stages"].append({"stage": "blob_head", **head})
            if head["status"] == 404:
                upload = client.request("POST", blob, file=weights)
                result["stages"].append({"stage": "local_blob_upload", **upload})
                if upload["status"] != 201:
                    raise RuntimeError(f"local blob upload failed: {upload}")
            elif head["status"] != 200:
                raise RuntimeError(f"unexpected blob HEAD response: {head}")
            parameters = {}
            for line in (directory / "Modelfile").read_text().splitlines():
                if line.startswith("PARAMETER "):
                    _, key, value = line.split()
                    number = float(value)
                    parameters[key] = int(number) if number.is_integer() else number
            created = client.request("POST", "/api/create", {"model": alias, "stream": False,
                "files": {weights.name: "sha256:"+manifest["weights_sha256"]}, "parameters": parameters})
            result["stages"].append({"stage": "create", **created})
            if created["status"] != 200 or created["body"].get("error"):
                raise RuntimeError(f"candidate create failed: {created}")
        elif not present:
            raise RuntimeError("candidate is absent; --install is required to import it")
        shown = client.json("POST", "/api/show", {"model": alias})
        result["stages"].append({"stage": "show", "response": shown})
        if manifest["weights_sha256"] not in shown.get("modelfile", ""):
            raise RuntimeError("Ollama alias does not identify the verified weight blob")
        rows = {}
        source = data / "val.jsonl" if data else ROOT / "artifacts/optimization/candidates/qwen05b-observer-20261102-v1/data-v1/val.jsonl"
        for line in source.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            rows.setdefault(row["kind"], row)
        for kind in ("proposals", "telemetry"):
            candidate_used = True
            started = time.perf_counter()
            answer = client.json("POST", "/api/chat", {"model": alias, "stream": False, "keep_alive": "2m",
                "messages": rows[kind]["messages"][:2], "options": {"num_ctx": 3072, "temperature": 0,
                    "num_predict": 192, "seed": 3407, "repeat_penalty": 1.1, "repeat_last_n": 3072}})
            raw = answer.get("message", {}).get("content", "")
            result["stages"].append({"stage": kind, "elapsed_s": time.perf_counter()-started, "response": answer})
            if answer.get("done_reason") == "length":
                raise RuntimeError("response was truncated")
            parse_verdict(raw, kind, "local", include_identity=False)
        result["status"] = "verified_two_role_load"
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        result.update(status="blocked", error=f"{type(exc).__name__}: {exc}")
    finally:
        if candidate_used:
            try:
                result["candidate_unload"] = client.json("POST", "/api/generate", {"model": alias, "keep_alive": 0})
            except (OSError, RuntimeError) as exc:
                result["candidate_unload_error"] = str(exc)
        try:
            after = client.json("GET", "/api/tags")
            result["tags_after"] = after
            def legacy(tags):
                return {m["name"]: m["digest"] for m in tags.get("models", []) if m["name"] != alias+":latest"}
            if before is not None:
                result["other_aliases_preserved"] = legacy(before) == legacy(after)
                if not result["other_aliases_preserved"]:
                    result.update(status="blocked", error="other alias inventory changed during verification")
        except (OSError, RuntimeError) as exc:
            result["tags_after_error"] = str(exc)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "model/scram-observer-slm-candidate")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--alias", choices=(ALIAS, "scram-observer-evidence-v2", "scram-observer-demo", "scram-observer-demo-f16"), default=ALIAS)
    parser.add_argument("--data", type=Path, help="New experiment validation data for its two-role verification")
    parser.add_argument("--resume", action="store_true", help="Preserve a previous failed check and retry its identical candidate")
    args = parser.parse_args()
    result = verify(args.directory, args.output, args.url, args.install, args.alias, args.data, args.resume)
    print(json.dumps({k: result[k] for k in ("status", "error", "other_aliases_preserved") if k in result}))
    return 0 if result["status"] == "verified_two_role_load" else 1


if __name__ == "__main__":
    raise SystemExit(main())
