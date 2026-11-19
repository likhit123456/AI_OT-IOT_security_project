# Shared observer SLM — continuation handoff

## Latest: project-wide evaluation found additional open issues (2026-11-18)

Read [PROJECT_EVALUATION.md](PROJECT_EVALUATION.md) before further demo work.
The evaluation confirmed negated-command misinterpretation, a reset/action
dispatch race, foreign-origin action acceptance, undeclared psutil dependency,
and premature 3D loading-overlay removal using isolated fixtures. It also found
incomplete writer ownership across ordinary live/manual-studio entry points and
stale status/documentation claims. These new findings remain open; evaluation
did not change application code. Fresh checks: 448 Python tests, 9 frontend tests,
TypeScript, CPU detector integrity and demo F16 deployment integrity passed.
The existing app/model session was preserved, with no live mutations or training.

## Functional demo bug review completed (2026-11-18)

Fixed app lifecycle, duplicate-launch/process ownership, stale dashboard readings,
polling reset races and malformed API/action error handling. Default pytest now
skips simulator-mutating tests unless `--run-live-simulator` is explicit.
448 Python tests and 9 frontend regressions passed; TypeScript and Vite build
passed. Deployed F16 hash/validation identity verified unchanged. Existing app
and model session left running; restart the control room and refresh the browser
to load the fixes. No new training or final model evaluation occurred.

Details, limitations and the initial live-test interruption/cleanup are in
[the functional review](artifacts/SOURCE_REVIEW_20261118.md).

## Code-only demo decision improvements completed (2026-11-18)

Both demo launchers now enable evidence review. Raw model predictions remain
visible. Constant/unsupported proposal alarms become unconfirmed and do not
throttle through the model channel; numerical/source evidence and independent
CPU/rules still identify clear faults. This changes the combined demo decisions,
not weights or the model's own accuracy. Legacy routing defaults remain unchanged.

Fresh nominal test: 8 raw proposal alarms, 0 supported, 8 unconfirmed; authority
stayed 1.0. A real injected rising-setpoint scenario still produced supported
drift findings. A targeted pressure-dropout rerun produced supported findings
in 13/13 sampled states in its selected interval, including 3 with a recent raw
normal model label. These are correlated development observations, not a new
accuracy estimate. The first flow-consistency miss and its corrective rerun are
both preserved. No exposed final test was rerun or used as a selection benchmark.

Read [the code-only review report](artifacts/optimization/candidates/qwen05b-observer-20261118-demo01/EVIDENCE_REVIEW_REPORT.md)
and [the review behavior guide](training/DEMO_EVIDENCE_REVIEW.md). Live evidence
is in `evidence-review-live-20261118-185953` and `evidence-review-live-20261118-190553` under the demo experiment.
158 relevant tests, TypeScript and frontend build passed; compiled assets served
HTTP 200. Test app/model processes stopped and injected sensor fault cleared;
simulator remains running. No training or model-weight change occurred.

RUN_SLM_CONTROL_ROOM.cmd starts the updated UI; refresh the browser after launch.
RUN_SLM_DEMO.cmd prints raw plus evidence-reviewed predictions. The old portable
model ZIP still contains raw-model examples, not the new repository review layer.
The earlier model-only final scores are historical and do not establish accuracy
of this newly composed detector. Production release remains unqualified.

## Latest: live simulator tested and startup recovery fixed (2026-11-18)

The user-started simulator was reachable. The original demo control room was
stuck after a transient startup EOF; its failed process state was preserved,
then only that identified failed app was stopped. Engine startup now retries
transient connection failures without repeated resets, and PlantService cleans
up failed initial connections. 83 relevant regression tests passed.

A real approximately five-minute no-reset Engine/control-room/SIS/observer run
completed with 5,208 last-sampled control cycles, 96 model calls, zero inference
failures, no observed SIS trip, native sensor freeze/clear and observer reset.
Control-cycle p95 was 9.45 ms (no relative legacy claim). Both roles were served.

**Live quality remains poor in some normal conditions:** all 16 sampled nominal
proposal verdicts were setpoint_drift; 11/16 nominal telemetry verdicts were
sensor_fault. This caused governance throttling. Do not present technical
integration success or earlier aggregate final scores as proof of live accuracy.
No model tuning, output filtering, authority-rule change or weight change was made.

Read [the live test report](artifacts/optimization/candidates/qwen05b-observer-20261118-demo01/live-smoke-20261118-182259/LIVE_TEST_REPORT.md)
and its adjacent report/behavior/trace files. Test app/model processes were
stopped and the injected sensor fault cleared; simulator remains running and
was not reset. RUN_SLM_CONTROL_ROOM.cmd can now be relaunched in normal PowerShell.
Original defaults/weights and earlier frozen independent results remain preserved.

## Latest: demo export and observer integration completed (2026-11-18)

The selected demo adapter was exported to F16 and Q8. F16 matched all 64
native-adapter development verdicts (93.64% macro-F1) and was selected; Q8 added
one normal false alarm. Application observer factory/worker/both roles/reset and
a standalone HTTP client were verified with actual inference. The opt-in
`RUN_SLM_CONTROL_ROOM.cmd` uses one owned local llama.cpp runner, the new observer
and a baseline operator. Normal application defaults remain V2/V3.

The F16 candidate and data/generation/build identities were frozen before the
once-only independent test opened the source study's sealed recordings. All
592 cases completed: 572 primary cases gave 94.33% macro-F1; native sensor recall
32/40 (80%); normal false alarms 10/40 (25%); actuator 40/40 and excursion 43/43;
constructed proposals 409/409. Pressure-dropout stress only 4/20; frozen-level
stress had no eligible parents. All outputs were valid. No tuning followed.

**These source-study sealed recordings are now exposed by this final evaluation.
Do not tune using these results or reuse them as an unseen selection set.**
Read [the latest next-steps report](artifacts/optimization/candidates/qwen05b-observer-20261118-demo01/NEXT_STEPS_REPORT.md)
and adjacent `demo-release-assessment.json`, `freeze.json`, final identity markers
and package hash records. The portable ZIP is 771,362,145 bytes and CRC-verified.
Model, optimizer, data and earlier records are preserved; 191 old source artifacts
and all three original specialist/operator model hashes were verified unchanged.

Production release remains unqualified: dropout/frozen-level checks, paired
specialist comparisons and relative live runtime/resource gates are incomplete
or weak. The agent could not start the live plant because Docker's engine pipe
is unavailable. Ordinary Ollama blob/create requests succeeded but /api/show
failed Access is denied; direct llama.cpp integration is verified. Optional
`SETUP_SLM_OLLAMA.cmd` retries the new isolated alias in a normal user shell.
The control-room launcher works independently of that import.

98 focused tests and Ruff passed. All task-owned servers were stopped. No
further training, deployment-default change, publication or upload occurred.
Older dated sections below describe earlier states and remain historical.

## Latest: short demo training completed (2026-11-18)

User explicitly requested short, good-enough demo training. Created a separate
`qwen05b-observer-20261118-demo01` experiment from evidence-v2 checkpoint 135,
with a fresh optimizer, learning rate 2e-5, at most 24 updates and small fixed
validation checks. Completed 24 updates in 581 seconds; selected checkpoint 24.
On 64 development cases: macro-F1 93.64%, normal and sensor recall 75% each,
actuator and excursion recall 100%, constructed proposals 32/32. Normal false
alarms remain 2/8. This met modest demo targets, not production release gates.
Fresh-process reload/inference passed both roles and 8/8 development smoke cases.

Read [demo results and commands](artifacts/optimization/candidates/qwen05b-observer-20261118-demo01/DEMO_RESULTS.md)
and `training/SLM_DEMO_TRAINING.md`. `RUN_SLM_DEMO.cmd` runs actual inference on
supplied JSON inputs without retraining, Docker or Ollama. Default examples are
previously used development cases, not independent final evaluation.

All prior 191 inventoried source artifacts and three original weight files were
verified unchanged. New weights, optimizer states, data, policy, reports, hashes
and reload evidence are saved. V2/V3 remain defaults. No sealed final test was
opened. Do not restart the old full pipeline or continue training automatically;
the requested short demo adaptation is complete. Older dated sections follow.

## Latest: evidence-v2 training completed and stopped (2026-11-09)

The new evidence-v2 run completed 180 steps and stopped at the user's request before post-training evaluation. All 80 recordings, checkpoint/recovery weights, optimizer states, data and logs are saved and integrity-checked. Best checkpoint 135: validation macro-F1 89.18%; last checkpoint 180: 83.18%. Selected telemetry falsely flags 29/40 normal cases; final checkpoint misses all 38 sensor faults. Not qualified for deployment. Original weights/defaults remain unchanged.

Read [the latest training results](artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/TRAINING_RESULTS_20261109.md) and its adjacent `training-stop-report-20261109.json` for exact hashes, limitations and continuation instructions. The pipeline's FileExistsError was the intentional stop guard, not a training failure. Do not restart now; wait for explicit user resumption. Older dated sections below are historical.

## Latest: port-allocation recovery after 53 completed recordings

Read [training/SLM_PORT_RECOVERY.md](training/SLM_PORT_RECOVERY.md). The user's
normal-user launcher saved 53/64 original recordings before Docker allocated
forbidden host port 55555. That container was rejected before any client
connection. Allocation now retries safely with ownership-checked cleanup.
All 53 recording hashes and the immutable original plan were verified; the
known collector-source journal change was audited with backups/prior identities.
Current state is `ready_to_resume_collection`.

Train/validation-only readiness inspection also found the original excursion
fixture settled near 2820 kPa: only one usable train excursion, none for val.
The classification criterion remains unchanged. A separately allocated,
pre-training 16-recording excursion supplement now has its own immutable plan,
source identities and mandatory real-fixture smoke before full collection.
The original 64-case plan and all completed recordings remain unchanged.
No sealed measurements were decoded or used for this design; no model training
occurred. 87 focused tests and Ruff pass. Resume the same normal-user launcher;
it skips completed recordings and checks support before any training.

## Automatic continuation after the reported collector traceback

Read [training/SLM_AUTOMATION.md](training/SLM_AUTOMATION.md) first. The traceback
the user supplied corresponds to the earlier five-sample failed attempt; current
collector code removes that abort and prints its hash/timing policy. Its complete
loop regression now verifies a scheduling pause, successful completion and
quarantine of the affected window. 81 focused tests pass.

`RUN_SLM_EVIDENCE.cmd` sets the project directory and runs candidate collection,
training, paired validation/final evaluation, export, static quality checks,
portable packaging and new-alias Ollama verification without confirmation
prompts. It was attempted in this sandbox and stopped at Docker preflight with
Windows permission denied. Actual state is in the new experiment's
`automation/status.json`; no improved model or completed recording was claimed.
The same launcher must execute in the normal user shell that can access Docker.
Do not bypass Docker ACLs or expose its API. V2/V3 defaults remain unchanged;
the automation explicitly keeps unmeasured runtime release gates blocked.

## Latest execution continuation: 2026-11-08

The user authorized all remaining work. **Ordinary Ollama HTTP import and actual
two-role inference now pass** for the unchanged frozen candidate; other aliases
were preserved and the candidate was unloaded afterward. It still fails quality.
Docker was started by the user, but both Docker pipes deny this sandbox access.

Read [training/SLM_EVIDENCE_V2_EXECUTION.md](training/SLM_EVIDENCE_V2_EXECUTION.md)
before continuing. Collection, screened data preparation, policy-aware training,
opt-in serving parity, quantization selection and a new independent holdout
freeze path are implemented. 76 focused tests and Ruff pass. These are tooling
checks, not new model results.

The first normal-user collection smoke passed the native sensor probe but aborted
on a scheduling pause. Its partial trace/error and previous plan are preserved.
The collector timing bug is fixed and native stuck excitation/probing was
declared before any completed recording or optimization. **The corrected smoke
retry is pending. No new training or independent final evaluation occurred.**

Pending normal-user command: `python -m training.collect_slm_evidence collect
--only train-normal-00` from the project root. Inspect a successful smoke before
running all collection with `--workers 4`. Do not reset the existing plant or
expose Docker's API to work around the sandbox. Check free RAM before training.
Run04 and V2/V3 remain preserved. Older dated records below describe prior states.

## Continuation on 2026-11-08

Resumed at the user's request. Completed the next experiment's supervision
audit, measured-only feature/prompt prototype, source-window candidate matching,
token budget check and fixed training/selection policy. See
[training/SLM_EVIDENCE_V2_REPORT.md](training/SLM_EVIDENCE_V2_REPORT.md) and
[experiments/slm_evidence_v2_policy.json](experiments/slm_evidence_v2_policy.json).
New local evidence lives in
`artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/`;
use its `supervision-audit-verified/` and `source-recovery-verified/` reports.

No new training or final-test evaluation occurred. Run04 remains frozen and
V2/V3 remain defaults. Next work requires reviewed supervision and new independent
recordings with actual aligned commands. WSL enumeration is denied and the Docker
engine is unavailable in this agent, preventing fresh isolated collection here.
Ordinary Ollama responds, but candidate CLI registration still returns Access is
denied. No existing simulator was reset and no inference server was started.
54 focused tests and Ruff passed. The earlier stopped-run record follows.

Stopped at the user's request on 2026-11-02. Main training and all candidate
evaluations are complete. Task-owned inference servers have been stopped.
Checkpoints, logs, exports and originals remain saved. No unrelated apps were
stopped. V2/V3 remain the default; no commit, push or publication occurred.

## Outcome

**A real fine-tuned Qwen2.5-0.5B shared observer now exists, but it failed the
quality requirements for replacing V2/V3.** It is an opt-in experimental model,
not an alias for old weights. Improving it requires a new experiment.

Read [SLM_BUILD_REPORT.md](SLM_BUILD_REPORT.md) for exact hashes, stage status,
environment, memory measurements and comparison tables. Reproduction commands
are in [training/SHARED_SLM_RECIPE.md](training/SHARED_SLM_RECIPE.md).

Project: `C:/Users/likhi/Downloads/SCRAM-Project`.
Branch: `optimization/shared-observer`. Preserve the existing dirty checkout.

## Saved work

The completed main run is
`artifacts/optimization/candidates/qwen05b-observer-20261102-run04/`.
Paths prefixed with `run04/` below abbreviate that directory.

| Item | Path |
| --- | --- |
| Selected trained adapter, tokenizer and optimizer | `run04/training/checkpoint-135/` |
| Later checkpoints, completion, selection, step logs | `run04/training/` |
| Merged BF16, F16/Q4/Q8 GGUF, converter logs and hashes | `run04/export/` |
| Full base/fine-tuned validation | `run04/base-validation.json`, `adapter-validation.json` |
| Quantization/specialist comparisons | `run04/q4-validation.json`, `f16-validation.json`, `q8-validation.json`, `legacy-validation.json` |
| Frozen model/test identity | `run04/freeze.json`, `final-data-manifest.json` |
| One completed final evaluation | `run04/final-test.json`, `final-evaluation.completed.json` |
| Real runtime and portable reload evidence | `run04/runtime-integration.json`, `portable-deployment-verification.json` |
| User-requested stopped state | `run04/paused-handoff.json` |
| Deployable GGUF, model card, metrics and runners | `model/scram-observer-slm-candidate/` |
| Portable ZIP and SHA sidecar | `run04/distribution/scram-observer-slm-candidate.zip` |
| Audited training/validation dataset | `artifacts/optimization/candidates/qwen05b-observer-20261102-v1/data-v1/` |
| Initial real smoke and diagnostics | `artifacts/optimization/candidates/qwen05b-observer-20261102-v1/` |
| Initial 45-step optimizer parent | `artifacts/optimization/candidates/qwen05b-observer-20261102-run02/training/checkpoint-45/` |
| Official trainable base | `artifacts/optimization/candidates/base-qwen2.5-0.5b/` |

Large weights/build directories are ignored by Git and remain local. Pushing
source will not distribute this model. No dataset or model was uploaded.

## What was completed

1. Used native Windows and isolated Python 3.11.9, leaving application Python
   unchanged. Verified real CUDA math and PEFT forward/backward, gradients,
   finite losses, changed weights, checkpoint saving and fresh-process reloads.
2. Fetched official Qwen/Qwen2.5-0.5B-Instruct at revision
   `7ae557604adf67be50417f59c2c2f167def9a775`, preserving tokenizer and Apache
   license. The user ran the downloader after agent network restrictions.
3. Audited the old 1,432/1,094 preparation. Excluded old proposal examples with
   inadequate scenario separation; rebuilt histories from disjoint recording
   groups. Deduplicated before balancing, retained complete aligned histories,
   and excluded source identity/simulator truth from model inputs. Current data
   is 720 train/475 validation. Regeneration reproduced exact dataset bytes.
4. Executed a real 16-step smoke, then the actual two-epoch/180-step experiment,
   covering 1,440 cumulative examples. Selected checkpoint135 with validation,
   preserving later checkpoints and optimizer states. One controlled class-loss
   adjustment followed poor step45 recall; no uncontrolled search occurred.
5. Evaluated the base and saved adapter on the same475 validation examples.
   Compared F16/Q4/Q8 and V2/V3 on the same90-case subset with actual inference.
   Retained invalid responses, raw outputs, per-class precision/recall,
   confusion matrices, latency, and separate SLM/CPU/combined metrics.
6. Exported BF16 -> F16 GGUF -> Q4_K_M, then Q8_0. Q4 failed the predeclared
   quantization degradation rule. Q8 passed and was frozen. Actual GGUF loads,
   chat template, token IDs, EOS/BOS and inference were verified.
7. Opened the sealed test only after freeze. Ran658 final cases once:471
   historical telemetry,147 constructed held-out proposals,40 separate stress
   cases. No test-driven retraining or threshold changes occurred.
8. Exercised the existing shared runtime with six real calls, both roles and
   one resident observer model. A real reset-race response was discarded.
   No plant writes. Operator/CPU/control/governance/signing/interlock unchanged.
   Original V2/V3/operator weight hashes were rechecked.
9. Built the portable ZIP, checked CRC and every bundled file's SHA, reloaded
   its deployment weights in a fresh server, and generated real responses for
   both tasks. Stopped that server for this pause.

Code checks:314 passed,2 skipped, one existing warning; Ruff and whitespace
checks passed. These checks do not establish model quality.

## Why replacement is unfinished

Full Transformers validation:66/66 proposal classifications correct, but
telemetry sensor recall26/120, actuator31/120 and excursion0/19;29/150 normal
false alarms. On the same-runtime comparison subset, V3 had50% sensor,100%
actuator and50% excursion recall; selected Q8 had16.7%,33.3% and0%.

Final primary telemetry: sensor3/120 (2.5%), actuator6/120 (5%), excursion114/120
(95%), normal65/111;46/111 normal false alarms. Frozen-level and dropout stress
each had0/20 correct sensor labels. Constructed proposals were147/147 correct,
but limited language templates, recording groups and class support restrict
generalization. Normal27 and provenance24 final cases are below30/class.

The candidate invents numerical explanations and misses direct command/position
disagreements. Some historical labels/explanations also require human review.
See [training/SLM_CANDIDATE_FAILURES.md](training/SLM_CANDIDATE_FAILURES.md).
Valid JSON, high template accuracy and CPU corrections cannot repair these
SLM weaknesses. Scores are not calibrated probabilities.

Selected Q8:531,067,808 bytes, versus1,972,095,680 bytes for V2+V3:about73.1%
smaller for observer deployment weights. Build artifacts/originals are retained,
so this has not freed that space locally. GPU/RAM savings were not established.

## Ollama blocker and working inference route

Ollama0.40.13 accepted a local GGUF blob/create call but failed reading its own
manifest with `Access is denied`. CLI create also failed. Explicit/relative
stores and an isolated legacy-layout manifest did not fix it. No ACL, drivers,
services or global settings were changed. See `run04/ollama-blocker.json`.

**The candidate is not verified registered in Ollama.** Its new alias was
verified through the existing Ollama-bundled llama.cpp server0.8.0-dev,
commit39febd2ad, CUDA v12, with25/25 layers on GPU. The minimal opt-in transport
is `monitor/llama_cpp_transport.py`; legacy remains the default.

## Start the saved candidate later

No training needs resuming to inspect this finished candidate:

```powershell
cd C:\Users\likhi\Downloads\SCRAM-Project
Get-Content SLM_BUILD_REPORT.md
Get-Content model\scram-observer-slm-candidate\README.md
python scripts/run_observer_slm.py
```

This verifies the GGUF SHA and starts only inference on11438. Ctrl+C stops it.
For a deliberately opt-in app session in a second terminal:

```powershell
$env:SCRAM_OBSERVER_MODE='unified'
$env:SCRAM_OBSERVER_MODEL='scram-observer-slm-candidate'
$env:SCRAM_OBSERVER_SCHEMA='slm-v1'
$env:SCRAM_OBSERVER_CONTEXT='3072'
$env:SCRAM_OBSERVER_BACKEND='llama_cpp'
$env:SCRAM_OBSERVER_URL='http://127.0.0.1:11438'
```

Do not attach normal app startup to an existing plant writer:it may reset its
simulator. Saved replay verification performed no plant writes. Rollback:set
`SCRAM_OBSERVER_MODE=legacy` and clear candidate model/schema/context/backend/
URL overrides. Keep the original specialists.

For ordinary Ollama, start it as the normal user and try in normal PowerShell:

```powershell
python scripts/install_observer_slm.py
```

This checks hashes and creates only the new candidate alias. It does not
promote the model or delete specialists. Successful Ollama loading still needs
real two-role inference verification; the command is a next troubleshooting
action, not a guaranteed fix for the agent's permission context.

## Next development session

1. Read this handoff, build report, recipe, failure inspection and applicable
   AGENTS.md if one appears. Verify branch/status; preserve the dirty checkout.
2. Keep run04 frozen. There is no interrupted main training. Do not resume
   training into it or tune on its final test. Use a new versioned experiment
   directory and independent holdout/procedure for future model development.
3. Audit telemetry target labels/explanations against measured traces. Review
   ambiguous normal transitions and fault variants; collect independent
   recordings rather than duplicate cases to meet support gates.
4. Investigate numeric/temporal failures. Consider validated measured-only
   numeric features alongside complete aligned histories, identically built
   in training and serving. Verify semantics, token budget and supervision.
5. Declare the next0.5B training budget/selection policy before running it.
   Compare saved weights fairly. A larger base may be proposed separately;
   none was substituted in this experiment.
6. Resolve normal-user Ollama registration and verify actual inference using
   a new alias/fixed context. Preserve specialists and operator artifacts.
7. After quality and independent class support improve, finish the deferred
   plant freshness/jitter, GPU contention, reload and resident-memory gates.
8. Only after gates pass, change defaults and remove specialist download
   requirements with concrete rollback evidence. No promotion occurred here.

Suggested next-session prompt:

> Continue the shared observer SLM work in SCRAM-Project. Read SLM_HANDOFF.md,
> SLM_BUILD_REPORT.md, training/SHARED_SLM_RECIPE.md and applicable AGENTS.md.
> Run04 is a completed frozen real0.5B candidate but failed telemetry quality.
> Preserve it and the checkout; do not retune on its final test or replace
> V2/V3 yet. Inspect the recorded data/failures and prepare a new versioned
> experiment with independent evidence, keeping protection paths unchanged.
