# Current project status — 2026-11-19

The seven code/UI findings in [the November evaluation](PROJECT_EVALUATION.md)
have been addressed in the working tree. Existing running application/model
processes were left in place. Restart the application to load the backend fixes;
older processes do not participate in the new ownership lease. No model weights
were changed and no training or live plant actions were run for this repair.

## Changes

1. **Command meaning:** negated, conditional and ambiguous control instructions
   fail before queueing or model fallback. Contradictory targets for the same
   valve are rejected. Clear affirmative commands remain supported. This is a
   conservative grammar, not general natural-language understanding.
2. **One simulator writer:** both the live Engine and manual Plant acquire the
   same host-wide lease keyed by resolved simulator endpoint, independent of
   their HTTP ports. Loopback aliases share ownership. This also covers the demo
   launcher, which starts the live Engine. The OS releases leases after process
   exit; failed acquisition cannot start a controller/reset. Ownership uses an
   exclusive loopback port derived from the endpoint. An unrelated occupied
   port or hash collision fails closed with an explicit startup error. Direct
   socket tools and explicit out-of-band attack scenarios remain outside this
   cooperative protection.
3. **Reset identity:** HTTP passes the expected run to Engine.submit. Validation
   retains that identity throughout queueing. Every action, including authority,
   executes on the controller thread and is cancelled if its run has changed.
   Requests already stale at submission receive HTTP 409.
4. **Browser boundary:** action requests require JSON content type, an allowed
   local Host, a matching Origin when present, and no cross-site Fetch Metadata.
   Vite's supported development port is fixed at 5173 with its original Host
   forwarded to the 8765 backend. Local command-line JSON clients remain usable.
   This is an origin boundary, not authentication for other local programs.
5. **Dependency declaration:** runtime requirements now pin `psutil==7.0.0`;
   development/CI requirements inherit it. Fresh-environment installation was
   attempted but the available package index returned no distributions for
   `fastapi==0.111.0`, so clean-install verification remains unconfirmed.
6. **3D feedback:** asset progress, runtime startup, errors and a two-minute
   timeout are distinct. Loading stays visible beyond 20 seconds. Download
   completion and iframe load do not mark Unity ready; runtime `calledRun` does.
   Error/timeout states offer retry, and the parent verifies message origin and
   iframe source. Reloading the view does not reset the simulator.
7. **Truthful status/docs:** observer configuration, recent inference evidence,
   CPU readiness and stale telemetry have separate descriptions. Architecture
   documentation now explains truth-channel use by L0/Keel and distinguishes
   the first latched cause from escalation for additional hazards.

## Verification

- Python suite: **489 passed, 2 skipped**, with the existing Starlette multipart
  deprecation warning. The skipped tests mutate the live simulator. Report:
  [JUnit results](artifacts/evaluation-fixes-tests-20261119.xml).
- Frontend regression suite: **18 passed**, including observer status and actual
  reactor bootstrap logic for slow, missing, failed and aborted loads.
- TypeScript checks and production Vite build passed; rebuilt assets are in
  `live/static/`. The standard config-bundling path encountered a Windows sandbox
  parent-directory restriction. The existing local Vite API build helper used
  the same React plugin, aliases, Tailwind settings and output directory.
- Playwright/Chromium smoke passed: both interfaces, controls, navigation,
  responsive layouts, recovery, and the real reactor HTML with a simulated Unity
  loader. Slow loading, timeout, missing loader, retry, readiness and message-source
  validation passed with no JavaScript errors. This does not test actual WebGL.
- Critical Python syntax/undefined-name checks and `git diff --check` passed.
- Default pytest temporary-directory access failed on a rerun; the successful
  suite used a fresh workspace-local `--basetemp` and disabled the shared cache.
- Fresh dependency installation remains unverified for the package-index reason
  above. No fresh hosted CI run was requested or triggered.

The tests use fake plant clients and intercepted browser requests; they do not
drive the running simulator. Regression coverage is in
`tests/test_evaluation_fixes.py`, `tests/frontend_unity.test.cjs`,
`tests/frontend_api.test.cjs`, and `tests/frontend_smoke.cjs`.

## Remaining evaluation work

The saved model-only telemetry evidence still includes 10/40 normal-telemetry
false alarms and 8/40 missed sensor faults. These figures are historical model-only
results, not measurements of the new combined model/rule system. No detection
accuracy improvement is claimed from these repairs.

The combined system still needs a preregistered independent evaluation using
unseen recordings, disjoint from development and previously exposed final tests.
Report confirmed alarms, misses, false alarms, unconfirmed outcomes, latency and
class support separately. Real Unity/WebGL rendering and the full live scenario
demonstration also remain to be exercised after a controlled application restart.

### Combined-system development diagnostic

On 2026-11-19, `training.evaluate_combined_development` replayed the existing
64-case model development report through the current CPU detector and evidence
review. The 32 telemetry windows gave 6/8 correct normal, 6/8 correct sensor
fault, 8/8 actuator fault, and 8/8 excursion classifications. The two normal
cases were unconfirmed, not confirmed false alarms or correct normal results.
One sensor fault was unconfirmed and one was classified as an excursion. All
eight constructed rationale-mismatch proposal cases were unconfirmed. These
small, correlated, previously used development cases cannot establish the
combined system's unseen accuracy; aligned model/CPU windows were assumed.
The exact per-case outputs and hashes are retained in
[the diagnostic report](artifacts/combined-development-diagnostic-20261119.json).
No thresholds or model weights were changed to fit these cases.

The earlier independent model-only test's source recordings are now exposed and
cannot be reused as an unseen holdout. Docker preflight for new, isolated
recordings failed because this sandbox was denied access to the Docker engine;
WSL access was also denied. The 80–90% target therefore remains unverified.

### Subsequent demo-only tuning

The evidence-review layer was adjusted without training or changing weights.
Repeated level readings that drop to zero and return while pressure remains
stable are flagged for investigation, retaining any separate excursion finding.
A narrowly scoped explicit claim that pressure and production are at their
design targets is checked against those numerical targets. On the same 64
development cases, sensor faults improved from 6/8 to 8/8 and constructed
rationale mismatch from 0/8 to 8/8; normal telemetry stayed 6/8, with two
unconfirmed, and both other telemetry classes stayed 8/8. These examples informed
the rules, so they are not an independent estimate and no unseen 80–90% result
is claimed. See [tuned diagnostic](artifacts/combined-development-tuned-20261119.json).
The demo launcher uses these rules after restart; ordinary V2/V3 defaults and
model files remain unchanged.

### Shared observer migration

Ordinary `python -m live` now starts the shared demo observer with the same
measured-evidence review used by the demo launcher. Its model and deployment
identity live in `model/scram-shared-observer/`; the local runner is started and
stopped by the application, and the model SHA-256 and development report are
checked before startup. Explicit `SCRAM_OBSERVER_MODE=none` runs CPU/rules only;
legacy V2/V3 mode is rejected. The operator LLM is separate and unchanged.
The default demo is not an independently qualified release. The earlier text
about V2/V3 remaining default is historical and superseded by this migration.

The former V2 and V3 GGUF files and V3 adapter were moved out of the project to
`../SCRAM-retired-observer-models-20261119/`, with a hash manifest for recovery;
historical cards, configurations and evaluation evidence remain. Any separately
installed Ollama aliases are outside the project and were not modified. A real
local model-server check returned eight valid results across both observer roles:
[check report](artifacts/shared-default-model-check-20261119.json). The post-migration
Python suite passed 495 tests with two live-simulator tests skipped. Full startup
against the live simulator was not exercised, and an existing process must be
restarted to load the change. The approximately 994 MB model weight is ignored
by Git and must be provided separately for a fresh checkout.
