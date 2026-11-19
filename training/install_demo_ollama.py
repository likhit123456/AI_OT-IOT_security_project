"""Retry ordinary Ollama import in the user shell, preserving prior failures."""
from __future__ import annotations

import json

from scripts.verify_observer_ollama import verify
from training.demo_observer_service import URL, start, stop
from training.launch_demo_control_room import ALIAS, deployment
from training.train_demo_observer import DESTINATION


def main():
    deployment()
    directory = DESTINATION / "app-deployment"
    start()
    try:
        result = verify(directory, DESTINATION / "ollama-f16-verification.json", URL,
                        install=True, alias=ALIAS, data=directory / "validation-data", resume=True)
        print(json.dumps({key: result[key] for key in ("status", "error") if key in result}))
        return 0 if result["status"] == "verified_two_role_load" else 1
    finally:
        stop()


if __name__ == "__main__":
    raise SystemExit(main())
