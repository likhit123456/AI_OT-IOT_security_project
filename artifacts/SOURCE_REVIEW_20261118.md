# Functional source review — 2026-11-18

Reviewed the demo control room, observer process management, frontend polling and
actions, and test entry points after the user reported unspecified runtime bugs.
This is a functional repair pass, not a claim that every possible bug is gone.

## Repairs

- Engine startup is idempotent. Shutdown signals and joins its control thread,
  closes the plant service, and closes the observer. A stopped control loop rejects
  actions instead of silently queueing them forever.
- The ordinary live entry point reserves its HTTP port before starting a plant
  writer, and releases the HTTP server on startup failure and shutdown.
- The demo launcher holds an OS socket mutex for its lifetime. A duplicate launch
  leaves the existing app/model alone. Stale lease metadata is archived; PID birth
  time and full command identify the recorded owner.
- Inference process reuse/termination checks full command identity and recorded
  birth time. Context changes prevent inappropriate reuse. A failed version query
  cannot leave a new runner behind; failures after launch terminate that runner.
  An occupied private port is rejected rather than adopting another listener.
- The dashboard has an independent 3.5-second stale-data watchdog while polling
  is pending. Controls use the same freshness check. Run identity is rechecked
  after a history resync, including a second reset during that request.
- API responses are checked before updating dashboard state. Invalid history or
  event records cannot poison cursors or render objects as notices. HTML proxy
  errors, incomplete action acknowledgements and rejected actions produce useful
  errors. Actions are not automatically retried.
- Live simulator tests now require explicit `--run-live-simulator`; the default
  suite neither probes nor resets the simulator through those tests. Frontend
  regression tests are included in the existing quality workflow.

## Verification

- Python suite: **448 passed, 2 skipped**, 17.84 seconds. The two skipped tests
  reset/drive a live simulator. One existing Starlette dependency deprecation
  warning remains. Final lifecycle-only confirmation: **10 passed**.
- Frontend regression tests: **9 passed** with `npm.cmd test` in `frontend`.
- TypeScript `tsc -b`: passed. Vite production build: passed via its API using
  the repository's React plugin, alias and output settings. The normal Vite
  config bundling path hits a parent-directory sandbox permission restriction;
  no global permissions or PowerShell execution policy were changed.
- New lifecycle/process-management Python files pass Ruff. Critical syntax and
  undefined-name Ruff checks pass for active first-party source. AST parsing
  passed for **143 Python source files**; archived artifacts, environments,
  vendored remote IO and model artifacts were excluded. The earlier broad Ruff
  report contains existing style findings and historical/vendor files; it is
  retained rather than mass-formatting preserved experiment sources.
- JavaScript syntax checks passed for the browser smoke script and both legacy
  app scripts. An interactive browser was unavailable, so no new visual browser
  or WebGL result is claimed.
- Existing running app returned HTTP 200 for HTML, the rebuilt JS/CSS and state.
  State was `running`, no error, fresh telemetry, 31,840 control cycles. This is
  a sampled health check, not a long-run guarantee. Evidence:
  `source-review-running-app-20261118.json`.
- The managed integration command refused a second launch because the existing
  app occupied port 8765. No second app/model was started; no fresh eight-case
  integration result is claimed. The existing session and model were preserved.
- The deployed F16 weight hash and its saved validation identity were reverified:
  `f155e25ce8898a332c11191d70f36a308cf12a8d14fce6743f11c792959f561d`.
  No retraining or model-accuracy claim is part of this review.

## Simulator and activation notes

The first broad pytest command entered the previously unguarded live integration
tests. That exact test process was stopped and its pressure sensor fault cleared;
the simulator was not subsequently reset for this review. The stopped-test record
is `source-review-stopped-tests-20261118.json`. Later runs used the new opt-in
guard. Read-only final HTTP checks did not write to the plant.

The user's existing app/model session was left running. Restart that control room
once to load the Python fixes, using `RUN_SLM_CONTROL_ROOM.cmd`, then refresh the
browser to load the rebuilt dashboard. The launcher preserves its no-reset mode.
The model's earlier final evaluation and production release limitations remain.
