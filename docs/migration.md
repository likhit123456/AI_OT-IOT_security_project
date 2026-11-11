# SCRAM migration log

Baseline on `main` is `6dc4cf7`. Append a dated entry when a refactor changes
paths, launch commands, or tracked artifacts. Record what callers need to update
and which checks ran. Use the [README](../README.md) for current setup and the
[repository guide](../FILE_STRUCTURE.md) for current paths.

## 2026-11-10

### Repository layout

- Move L1 from `enforcer/` to `l1_enforcer/` and L2 from `monitor/` to
  `l2_monitor/`. The former root `l1_regulator.py` is now
  `l1_enforcer/regulator.py`. Update imports in scripts, tests, and notebooks.
- Move `contracts.py` to `shared/contracts.py`. Move `l1_stub.py` and
  `run_demo.py` to `legacy/`. Run the historical demo with
  `python -m legacy.run_demo`. The OpenPLC diagnostic moved from
  `modbus_test.py` to `scripts/diagnostics/modbus_test.py`.
- Move `requirements.txt` to `requirements/runtime.txt` and the former
  `requirements-dev.txt`, `requirements-eval.txt`, and
  `requirements-train.txt` to `requirements/dev.txt`, `requirements/eval.txt`,
  and `requirements/train.txt`, respectively.
- Move architecture, demo, frontend, and upstream guides to `docs/`.
  Move the accuracy, quality, and project review reports to `docs/reports/`.
  Move the code of conduct and security policy to `.github/`.
- Remove the shelved C++ implementation in `reference/sim_cpp_unused/` and
  the inherited screenshots formerly in `images/`. Upstream attribution is
  in [docs/upstream.md](upstream.md).

### Runtime and verification

- Run the control room with `python -m live` or the manual studio with
  `python -m sim_app` from the repository root. Compose runs the prebuilt
  GRFICS simulator. Ollama configuration is opt-in with `--configure-ollama`.
- Update the physics detector and holdout evaluator to read the feature module
  at its new path. Update the active release hashes for the import change.
  The 215-case holdout reproduced the saved predictions and scores.
- Bind the control-room HTTP port before starting the control loop. CI checks
  both launcher help commands. All 280 offline tests passed across two runs.
  Four local HTTP tests required running outside the sandbox. The live
  simulator and plant integration tests were not run.

### Documentation and artifact cleanup

- Update the architecture guide to describe the deployed layers, signing,
  safe-hold, and re-arm behavior. Use this log in place of the one-time move
  manifest, refactor notes, and validation snapshot.
- Stop tracking the TypeScript build cache and remove the unused frontier
  backup. Keep the historical physics holdout report under
  `model/telemetry-physics/holdout-evaluation.json` and remove its byte-identical
  copy under `artifacts/accuracy-v2/`.
- Remove the unused Modbus/TCP captures `modbus.pcap` and `fuzz1.pcap`, which
  had moved from the root to `data/samples/network/`. They remain in Git
  history for manual protocol analysis.
