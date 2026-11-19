# Observer optimization checkpoint

- Source request: Downloads/SCRAM_Codex_Optimization_Prompt.md (no matching PDF found).
- Branch: optimization/shared-observer; pre-existing artifacts/project-guide/ preserved.
- Baseline: 280 offline tests passed in 17.05 s, one multipart deprecation warning.
- No AGENTS.md found in workspace/project. Historical evidence and weights remain untouched.
- GPU: RTX 3050 laptop, 6144 MiB, driver 572.47; observed total desktop VRAM 1234 MiB.
- Ollama localhost:11434 probe timed out. CIM CPU/RAM inventory denied by sandbox.
- Confirmed CPU detector depends on successful LLM parsing in existing Observer._run.
- Unified candidate must remain opt-in pending real specialist comparisons.

## Delivery checkpoint

- Implemented independent CPU sampling and legacy/unified/none runtime modes.
- Unified: one bounded fair worker, separate role schemas/history, run/generation/
  input identities, explicit unavailable states, stale/deadline rejection, no legacy fallback.
- Fixed operator instruction/reset/expiry races and reset observer histories on mode changes.
- Final offline verification: **311 passed, one existing multipart warning, 12.94 s**.
  Command: `python -m pytest -q --ignore=tests/test_interlock_truth.py --basetemp artifacts/pytest-optimization-complete -p no:cacheprovider -W error::pytest.PytestUnhandledThreadExceptionWarning`.
- Ruff passes for all five added Python files; JavaScript syntax check passes.
  Existing full-file lint findings unchanged: policy 29, engine 27 (see comparison JSON).
- Intermediate temp-directory fixture failures were reproduced and resolved with
  workspace-local temp directories. A new manual-startup attribute regression was
  fixed by initializing observer state before connection; the final suite passes.
- Real approved CPU artifact: 106/109 validation windows correctly classified,
  0/30 normal false positives; 2 missed excursions, 1 sensor/actuator confusion.
  Latest CPU p50/p95: 1.219/2.101 ms. No training/model selection used the test set.
- Real measured-input replay (34.94 s, 69 samples) with mode none: zero LLM calls,
  zero suspicion, feed p50/p95/max 0.049/2.763/3.223 ms. Not full-loop timing.
- Multitask preparation exercised successfully: 1432 train, 1094 validation;
  training/export not run. Source evidence and weights retained, SHA-256s recorded.
- Browser smoke blocked: Playwright unavailable, offline cache miss and npm EACCES.
- Isolated C++ check blocked: binary absent; compilation blocked by WSL E_ACCESSDENIED.
- Real LLM quality, sustained memory/runner residency, operator contention, reset/
  reconnect integration and control jitter remain unmeasured. No VRAM/RAM saving
  claimed. Observer disk savings: 0 bytes (rollback weights retained).
- Report and exact run/evaluate/train/rollback commands: OBSERVER_OPTIMIZATION.md.
- All changes remain local and uncommitted on optimization/shared-observer.
