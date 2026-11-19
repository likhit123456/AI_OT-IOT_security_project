# Observer consolidation and optimization

Implemented on `optimization/shared-observer`, locally only. Source request:
`Downloads/SCRAM_Codex_Optimization_Prompt.md`; no matching PDF was present.
Existing untracked `artifacts/project-guide/` was preserved. No weights, frozen
evidence, approval hashes, operator prompts/weights, global Ollama settings,
remote branches, or running plant sessions were changed.

## Architecture and findings

Observer **v2/v3 are model versions**, not the L2/L3 architecture layers.
`monitor/observer.py` remains the legacy comparison path. V2 reads six operator
proposals and detects provenance violations, rationale mismatches and setpoint
drift. Its `suspicion()` enters the existing Governor/Keel path; it cannot issue
commands or signatures. V3 reads sixteen measured samples, one per second,
paired with the L1 commands that produced them. Its telemetry labels are advisory.
The operator is a separate model emitting supervisory setpoints only.

The retained split was deliberate: `experiments/observer_v3_proposal_replay.json`
records v3 proposal false alarms where v2 remained normal. V3 also has known
telemetry weaknesses: its model card records 21.67% historical sensor-fault recall.
These historical results were inspected, not recomputed or rewritten. The frozen
CPU physics detector has separate, stronger evidence and must not be credited
to the LLM. Its existing feature and release checks remain byte-for-byte intact.

Previously the CPU correction ran inside `Observer._run` only after valid LLM
output. The live runtime now runs it independently through `CPUObserver`, using
the original V3 measured/commanded window and approved predictor. Missing commands,
sampling gaps, non-finite values and incomplete windows produce explicit statuses.
This adds detection during LLM failure without changing thresholds or authority.

`monitor/observer_runtime.py` composes CPU evidence with one of three LLM modes:

| Mode | Observer model routing | Default/status |
| --- | --- | --- |
| `legacy` | V2 proposal specialist, V3 telemetry specialist, original per-path fallback | Default; rollback/comparison |
| `unified` | One explicitly selected model, two stateless task modes, fixed context | Opt-in candidate; not quality-approved |
| `none` | No observer LLM calls or discovery | CPU/rules remain; operator configured separately |

The unified worker has at most one active call and two pending requests (one per
task). New work replaces pending work of the same task; when both tasks are
pending they alternate. There is one persistent daemon worker per runtime, created
on first work. No control tick creates a thread. Input histories and sampling
remain separate; unchanged proposal histories do not trigger repeated calls.
Telemetry continues sampling so gradual faults remain observable. Closing/resetting
cancels pending work; the HTTP timeout bounds an active call, whose stale response
cannot publish. Shutdown does not forcibly kill backend runners.

Each result is locally tied to run ID, task, generation, input sequence, simulation time,
queue time and deadline. Late results after reset, changed instructions, model
switches, manual-mode changes or superseded proposals are rejected. Role-specific
JSON schemas constrain labels and echo identity; semantic validation rejects wrong
identity, non-finite/out-of-range scores, missing/extra fields and long reasons.
External rationales stay in user JSON, with bounded length, separate from system
instructions. Injection tests validate routing and parsing; resistance of real
model behavior remains untested.

The independent CPU verdict is the primary displayed telemetry result when ready.
`cpu_telemetry`, `llm_telemetry`, `cpu_status`, and `disagreement` retain separate
evidence. Telemetry never contributes to `suspicion()`. Deterministic monitors,
governance, PI regulation, Keel, signing, provenance and L0 protection retain their
roles. `live/` and the direct-control `sim_app/` remain distinct; no UI redesign or
framework change was included.

Confirmed operator lifecycle bug: an old response could replace cached setpoints
after an instruction changed. `LLMOperator` now captures the instruction generation
when scheduling and rejects stale/expired completions. Reset holds while awaiting
a fresh decision and keeps the existing single-call guard. Re-arm waits for an
accepted post-reset completion before priming the sequential monitor. Discarded
responses remain audit events and are not presented as a new chat answer.

## Measured evidence and limits

Hardware inventory: NVIDIA RTX 3050 6 GB Laptop GPU, 6144 MiB device memory,
driver 572.47, CUDA driver capability 12.8; 16 logical CPUs; 16,438,312,960 bytes
system RAM. Python 3.13.5, Node 24.7.0, Windows 11. The initial inventory observed
15,229,296,640 bytes system RAM occupied, about 1.2 GB available, and 1111 MiB
total GPU usage. These include other applications. No Ollama runner was observed;
localhost:11434 timed out. Runtime version, installed aliases and runner-resident
memory could therefore not be measured. CIM inventory was denied; portable
platform/psutil inventory supplied the available CPU/RAM information.

| Measure | Before | After / candidate |
| --- | --- | --- |
| Offline regression suite | 280 passed, 17.05 s | 311 passed, 12.94 s; one existing multipart warning |
| CPU execution prerequisite | Valid telemetry LLM output | Independent sampled CPU window |
| Observer GGUF bytes on disk | 1,972,095,680 (V2 + V3) | Unchanged; rollback artifacts retained |
| Operator GGUF bytes | 986,048,000 | Unchanged |
| Unified selected checkpoint bytes | Not applicable | V2: 986,047,872; selection size only |
| Resident observer weights/KV RAM/VRAM savings | Unmeasured | Unmeasured; no claim |
| Real cold/warm inference, p50/p95 verdict age, throughput, tokens, queue contention | Unavailable backend | Unmeasured |
| Full control-loop timing/jitter and reset/reconnect integration | No isolated plant available | Unmeasured |
| Energy | No reliable sampled energy counter | Not measured |

The 34.94 s CPU-only measured-recording replay completed with zero observer LLM
calls and zero proposal suspicion. Across 69 feed calls the median/p95/max feed
costs were 0.049/2.763/3.223 ms. This covers window collection and CPU detection
when due, not the full control loop, and supplies no before/after savings claim.

Test suite durations are not application speed measurements. Selecting one file
does not prove runtime savings; retaining files means disk savings are **0 bytes**.
The extra context and CPU sampling cost must be included in future comparisons.
Do not attribute desktop VRAM changes to SCRAM or infer KV memory from file size.

A fresh, deterministic CPU-only evaluation used existing **validation**, not final
test data: 109 telemetry windows, 106 correct, 0/30 normal false positives. Recall:
actuator fault 30/30, excursion 17/19, normal 30/30, sensor fault 29/30. Two excursions
were classified normal; one sensor fault was classified actuator fault. Raw counts,
precision/confusion matrices and measured p50/p95 CPU latency are in
`artifacts/optimization/validation-cpu.json` (about 1.2/2.1 ms in the initial run).
This is predictor timing on static windows, not end-to-end detection delay.
The validation split has only 19 excursions, below the new minimum class-support
gate. Frozen-level/dropout runtime scheduling and delayed detection remain to be
measured on whole recordings. No tuning or weight update was performed.

## Candidate choice and promotion gates

`experiments/unified_observer_gates.json` declares gates before candidate tuning:
no per-class quality loss against each specialist, no extra proposal false alarms,
no extra combined-path missed faults, <=1% invalid outputs, at least 30 examples
per class, no weak-case regressions, no slower p95 verdict age, <=5% p95 control
tick increase, one observer runner with no sustained reloads, observer resident
memory <=80% of legacy and no increase in process/runner RAM. Run at least five
minutes with operator contention and reset/reconnect coverage. These are targets,
not observed improvements. Missing measurements block promotion.

Shortlist, in practical evaluation order:

1. Existing V2 Q4_K_M, already local. First shared-checkpoint experiment because
   it retains proposal-specialist weights; its ability to use the V3 telemetry
   format and new identity schema is **not established**. It is not a replacement
   model approval. Fixed 3072-token context is used by both tasks.
2. Qwen2.5-0.5B-Instruct at Q4_K_M: smaller instruction-model candidate, Apache-2.0
   according to the [official model card](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct).
   Not downloaded, benchmarked or selected for production.
3. Qwen2.5-1.5B-Instruct Q4_K_M: same-family stock control candidate, Apache-2.0 per
   [official model card](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct).
   Not downloaded or benchmarked. Existing trained V2/V3 use this family.

Ollama supports schema-constrained output in its
[documented structured-output interface](https://ollama.com/blog/structured-outputs).
Compatibility of the installed backend build cannot be confirmed while it is
unreachable. Quantization/identity-output/context quality must be evaluated rather
than assumed. No new model was produced, so there is no new trained-model card or
claim of distillation. The training script is a candidate pipeline, not evidence
that training/export succeeded.

## Run and rollback

From the repository root, in a **new isolated simulation session**:

```powershell
# Validated specialist routing remains the default.
$env:SCRAM_OBSERVER_MODE = 'legacy'
python -m live --no-browser

# Candidate: one observer model, both task modes, unchanged operator configuration.
$env:SCRAM_OBSERVER_MODE = 'unified'
$env:SCRAM_OBSERVER_MODEL = 'scram-observer'
$env:SCRAM_OBSERVER_CONTEXT = '3072'
python -m live --no-browser

# Disable observer LLMs; keep CPU detection, rules and the separately configured operator.
$env:SCRAM_OBSERVER_MODE = 'none'
python -m live --no-browser

# Roll back routing in a fresh session.
$env:SCRAM_OBSERVER_MODE = 'legacy'
Remove-Item Env:SCRAM_OBSERVER_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:SCRAM_OBSERVER_CONTEXT -ErrorAction SilentlyContinue
```

Normal live startup resets its configured simulator. Do not use these commands
against an existing user's session. Use separate simulator instances/ports for
comparisons. `--no-reset` attaches without resetting but still controls the plant.
The environment selection is read when constructing the runtime; it is not a
hot-switch API. No legacy models are silently loaded in unified/none modes.
Models already resident in a shared Ollama server are not unloaded by SCRAM.
Use a dedicated runner/backend to establish residency without disturbing users.

## Reproduce evaluation and training preparation

```powershell
# Inventory only; no inference, simulator reset, unloading, or settings changes.
python scripts/benchmark_observer_runtime.py --output artifacts/optimization/inventory-new.json

# CPU-only validation, default seed 3407; does not read the sealed test.
python -m training.evaluate_shared_observer --cpu-only --per-class 30 --output artifacts/optimization/cpu-new.json

# Paired specialist/shared real inference when the local backend is reachable.
python -m training.evaluate_shared_observer --model scram-observer --per-class 30 --output artifacts/optimization/v2-shared-validation.json

# After model selection is frozen, run the sealed test once; never tune against it.
python -m training.evaluate_shared_observer --model scram-observer --sealed-test --per-class 100000 --output artifacts/optimization/final-test.json

# Replay the SAME measured runtime recording and harness for all configurations.
python scripts/benchmark_observer_runtime.py --mode legacy --replay artifacts/optimization/runtime-replay.jsonl --output artifacts/optimization/legacy-runtime.json
python scripts/benchmark_observer_runtime.py --mode none --replay artifacts/optimization/runtime-replay.jsonl --output artifacts/optimization/none-runtime-new.json
python scripts/benchmark_observer_runtime.py --mode unified --replay artifacts/optimization/runtime-replay.jsonl --output artifacts/optimization/shared-runtime.json

# Preparation was exercised; training/export were NOT run.
python -m training.train_shared_observer --destination artifacts/optimization/candidates/run-001
# In the separate training environment, after resource/backend issues are resolved:
python -m training.train_shared_observer --stage train --destination artifacts/optimization/candidates/run-001 --max-steps 200
```

The supplied short replay is 69 measured samples from an existing recording,
with truth/fault metadata removed, no proposal inputs, and zero-filled unused
cost. Its provenance is recorded beside it. It is a runtime smoke workload,
not the required sustained proposal/operator contention or fault-quality suite.
For those comparisons provide a >=300 s recording containing actual measured
states, signed-command positions and operator intents. Replay accepts JSONL rows
`{t, state, commanded, intent?}` using the contract field names and SI/project
engineering units. It never writes a simulator. Inventory samples include total
device memory, process RSS, available host RAM, CPU load and Ollama `/api/ps`;
they are coarse samples, **not guaranteed peak counters**. Counters do not separate
weights from KV allocations. Request audit events include queue wait, age, token
counts and backend load time where available. Records are bounded to 100 events.
Paired quality evaluation loads comparison models deliberately; it is not evidence
of unified-only residency. The harness records null for unsupported measurements.

Training preparation verifies the existing recording split manifest/hashes,
checks disjoint recordings, deduplicates user inputs across training/validation,
balances training by task/class without mixing histories, and reuses the serving
prompt builder. It produced 1432 training and 1094 validation examples in the
local preparation run. It never reads the sealed test, fits preprocessing on it,
or treats teacher predictions as ground truth. Existing labels remain existing
labels, not a new human verification. Unsloth/TRL QLoRA training and GGUF export
are optional stages; their installed-version/API/GPU compatibility is untested.
Register any new GGUF under a **new alias** and repeat the quality/quantization
and runtime gates. Do not replace the operator or specialist files.

## Verification and remaining work

Offline regression tests include governance, Keel, interlock units, provenance,
role routing, fair bounded scheduling, cancellation, invalid telemetry, schema
failures, prompt-injection-like rationales, backend recovery and lifecycle races.
Model transport is mocked in those tests. CPU validation uses the real approved
CPU artifact. Run:

```powershell
python -m pytest -q --ignore=tests/test_interlock_truth.py --basetemp artifacts/pytest-optimization-your-run -p no:cacheprovider -W error::pytest.PytestUnhandledThreadExceptionWarning
python -m ruff check monitor/observer_runtime.py scripts/benchmark_observer_runtime.py training/evaluate_shared_observer.py training/train_shared_observer.py tests/test_observer_runtime.py
node --check live/static/control-room.js
node tests/frontend_smoke.cjs
```

The existing full-file lint violations in `policy.py` and `engine.py` are reported
separately in `existing-lint-comparison.json`; no cosmetic mass rewrite was done.
An intermediate suite run had three fixture errors from inaccessible Windows temp
directories; the workspace-local `--basetemp` rerun passed. The browser smoke
command could not run because Playwright was missing, its cache was unavailable
to installation, and npm network installation returned EACCES. JavaScript syntax
checks pass. The isolated C++ smoke initially found no built binary; rebuilding
was blocked by WSL E_ACCESSDENIED. The two live-reset interlock tests were
deliberately excluded; no running session was manipulated.

Remaining release blockers: accessible local real inference, specialist/shared
per-class quality and weak-case results, sustained real runner residency and
weights/KV/process memory, cold/warm timings, operator contention, full-loop jitter,
reset/reconnect simulator integration, and browser smoke. Optional training/export
also remains unexecuted. The default is therefore **legacy**, with independent
CPU observation active. This delivery does not claim the specialists have been
replaced or that the full real-model optimization evaluation is complete.
