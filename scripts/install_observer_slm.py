"""Verify and register the built candidate under a NEW Ollama alias.

Does not remove legacy/operator models, change global settings or start a plant.
"""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path,
                        default=Path(__file__).resolve().parents[1] / "model/scram-observer-slm-candidate")
    parser.add_argument("--host", help="Optional Ollama host for this command only")
    args = parser.parse_args()
    manifest = json.loads((args.directory / "deployment.json").read_text(encoding="utf-8"))
    model = args.directory / manifest["weights_file"]
    with model.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != manifest["weights_sha256"]:
        raise ValueError("candidate GGUF checksum mismatch")
    if manifest["alias"] != "scram-observer-slm-candidate":
        raise ValueError("this installer registers only the new candidate alias")
    with (args.directory / "Modelfile").open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != manifest["modelfile_sha256"]:
            raise ValueError("candidate Modelfile checksum mismatch")
    environment = dict(os.environ)
    if args.host:
        environment["OLLAMA_HOST"] = args.host
    subprocess.run(["ollama", "create", manifest["alias"], "-f", str(args.directory / "Modelfile")],
                   env=environment, check=True)
    print(f"Registered verified weights as {manifest['alias']}. Legacy models retained.")
    print("Opt in with SCRAM_OBSERVER_MODE=unified, SCRAM_OBSERVER_MODEL="+manifest["alias"]+
          ", SCRAM_OBSERVER_SCHEMA=slm-v1 and SCRAM_OBSERVER_CONTEXT=3072.")
    if args.host:
        print("Set SCRAM_OBSERVER_URL to the matching http://host:port for that observer server.")


if __name__ == "__main__":
    main()
