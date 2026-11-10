# Repository navigation

Start with [setup and usage](README.md). Run commands from the repository root.

## Application

- `live/`: main SCRAM control-room engine, server and deployed web assets. Start with `python -m live`.
- `frontend/`: React source and build configuration. Its output is served from `live/static/`.
- `l0_plant/`: plant connection, provenance gate, fault injection and safety mechanisms.
- `l1_enforcer/`: deterministic enforcement; `regulator.py` contains L1 regulation and `keel.py` contains command constraints.
- `l2_monitor/`: behavior, governance, observer and telemetry monitoring.
- `l3_brain/`: supervisory operator logic and model prompts.
- `shared/contracts.py`: shared command/state contracts.
- `sim_app/`: optional separate manual studio; start with `python -m sim_app`.

## Development and deployment

- `requirements/runtime.txt`: application dependencies.
- `requirements/dev.txt`: runtime plus test/development dependencies.
- `requirements/eval.txt`: CPU model evaluation/training dependencies.
- `requirements/train.txt`: heavy LLM training dependencies; preferably use a separate environment.
- `tests/`: existing Python tests and browser smoke checks.
- `scripts/`: maintenance and evaluation helpers; `diagnostics/modbus_test.py` is an optional legacy OpenPLC probe.
- `docker-compose.yml` and `docker/`: current prebuilt simulator deployment.
- `simulation/`: maintained upstream simulator source and optional local web/container build inputs.
- `packaging/`: optional Debian packaging.
- `.github/`: CI, issue templates, security policy and code of conduct.

## Research and retained historical material

- `training/`: dataset generation, training scripts and datasets. `design_point.json` is ALSO a runtime dependency; do not remove this directory wholesale.
- `model/`: model setup, exports, deployed CPU models and validation evidence.
- `experiments/`: experimental scripts and saved results.
- `artifacts/`: evaluation evidence and historical cleanup reports. Some files are consumed by tests.
- `legacy/`: historical minimal control loop. Run its demo with `python -m legacy.run_demo`; collectors and experiments still import its L1 stub.
- `data/samples/network/`: preserved packet captures.
- `docs/`: architecture, runbook, frontend notes and upstream attribution.
- `docs/reports/`: historical review and accuracy documents.

## Important boundaries

This refactor preserves every original file; no optional feature or dataset was deleted. Existing package names and launch commands remain, except the historical root demo is now a module. The L0/L1/L2/L3 design is unchanged.

Built frontend files and model evidence intentionally remain in their existing locations. Moving them requires coordinated build/runtime changes. The original four dependency sets remain separate; only their location and names changed.

See [refactor notes](docs/refactor-notes.md) for changes and manual verification. [File move manifest](docs/file-moves.json) records original and new paths.
