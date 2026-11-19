"""Run the verified GGUF directly in local llama.cpp; never starts the plant."""
from __future__ import annotations

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
    parser.add_argument("--port", type=int, default=11438)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    folder = args.directory.resolve()
    manifest = json.loads((folder / "deployment.json").read_text(encoding="utf-8"))
    weights = folder / manifest["weights_file"]
    if weights.parent != folder:
        raise ValueError("weights must be inside the candidate directory")
    with weights.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != manifest["weights_sha256"]:
            raise ValueError("candidate weight hash does not match deployment manifest")
    binary = args.binary or Path(os.environ["LOCALAPPDATA"]) / "Programs/Ollama/lib/ollama/llama-server.exe"
    environment = dict(os.environ)
    if not args.cpu:
        library = binary.parent / "cuda_v12"
        if not (library / "ggml-cuda.dll").is_file():
            raise FileNotFoundError("CUDA v12 backend absent; pass --cpu or a compatible --binary")
        environment["PATH"] = str(library)+os.pathsep+str(binary.parent)+os.pathsep+environment["PATH"]
        environment["GGML_BACKEND_PATH"] = str(library / "ggml-cuda.dll")
    command = [str(binary), "-m", str(weights), "--host", "127.0.0.1", "--port", str(args.port),
               "-c", "3072", "--parallel", "1", "--jinja", "--cache-ram", "0", "-t", "8",
               "--alias", "scram-observer-slm-candidate", "-ngl", "0" if args.cpu else "99"]
    if not args.cpu:
        command += ["--device", "CUDA0"]
    print(f"Starting local candidate at http://127.0.0.1:{args.port}; Ctrl+C stops it.", flush=True)
    subprocess.run(command, env=environment, check=True)


if __name__ == "__main__":
    main()
