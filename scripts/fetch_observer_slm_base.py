"""Fetch only the official pinned trainable 0.5B checkpoint; never uploads data."""
import json
from pathlib import Path

REPO = "Qwen/Qwen2.5-0.5B-Instruct"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "artifacts/optimization/candidates/base-qwen2.5-0.5b"


def main():
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=REPO, revision=REVISION, local_dir=str(DEST),
                      allow_patterns=["*.json", "*.safetensors", "*.txt", "LICENSE", "README.md"])
    (DEST / "source-revision.json").write_text(json.dumps({
        "repo_id": REPO, "revision": REVISION, "license": "apache-2.0",
    }, indent=2), encoding="utf-8")
    print(DEST)


if __name__ == "__main__":
    main()
