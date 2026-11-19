# SLM build report ? stopped at the user's request

## Latest: functional demo bug review completed (2026-11-18)

App lifecycle, duplicate launches, process identity, dashboard freshness and API
error handling repaired without training. 448 Python tests and 9 frontend tests
passed; TypeScript and Vite build passed. Existing running app served the rebuilt
assets and fresh state successfully. Model F16 hash and saved validation identity
verified unchanged. See [the review report](artifacts/SOURCE_REVIEW_20261118.md)
for exact checks, limitations and simulator-test interruption/cleanup. Existing
app/model session was preserved; restart and browser refresh activate all fixes.

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

## Latest experiment: completed training, validation results and saved artifacts (2026-11-09)

The new evidence-v2 run completed 180 steps and stopped at the user's request before post-training evaluation. All 80 recordings, checkpoint/recovery weights, optimizer states, data and logs are saved and integrity-checked. Best checkpoint 135: validation macro-F1 89.18%; last checkpoint 180: 83.18%. Selected telemetry falsely flags 29/40 normal cases; final checkpoint misses all 38 sensor faults. Not qualified for deployment. Original weights/defaults remain unchanged.

Read [the latest training results](artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/TRAINING_RESULTS_20261109.md) and its adjacent `training-stop-report-20261109.json` for exact hashes, limitations and continuation instructions. The pipeline's FileExistsError was the intentional stop guard, not a training failure. Do not restart now; wait for explicit user resumption. Older dated sections below are historical.

A real Qwen2.5-0.5B shared observer candidate has been fine-tuned, saved, reloaded,
merged, quantized and evaluated with actual inference. **It failed replacement
quality gates. V2/V3 remain the default and their weights are unchanged.**
No training is still required to finish this first candidate; improving it is a
new experiment. See [SLM_HANDOFF.md](SLM_HANDOFF.md) for the detailed continuation.

Project: `C:/Users/likhi/Downloads/SCRAM-Project`.
Branch: `optimization/shared-observer`. Existing uncommitted work was preserved;
no commit, push, publication or dataset upload was performed.

## Stage status

| Stage | Status | Evidence |
| --- | --- | --- |
| Inspect/preserve prior work | Complete | Continued existing observer runtime; no AGENTS.md found in applicable paths |
| Official base, pinned revision/license | Complete | Qwen/Qwen2.5-0.5B-Instruct, revision `7ae557604adf67be50417f59c2c2f167def9a775`, Apache-2.0 |
| Native CUDA and real PEFT backward | Complete | Isolated Python 3.11 environment; preflight.json/environment.json |
| Data audit, tokenizer/template, response-only loss | Complete | 720 train / 475 validation; exact regenerated bytes; no temporal truncation |
| Real 16-step smoke, save/reload | Complete | 32 examples; 336 adapter tensors changed; real two-role reload inference |
| Main training and checkpoint selection | Complete | 180 cumulative optimizer steps / 1,440 examples; selected step 135, 1,080 examples |
| Base versus saved fine-tuned model | Complete | Same 475 validation cases, greedy generation, batch 8 |
| Merge, F16/Q4/Q8 export and real GGUF inference | Complete | Higher-precision artifacts retained; Q8 selected by validation |
| V2/V3 comparison | Complete | Same fixed 90-case validation subset, same llama.cpp backend |
| Freeze and one final held-out run | Complete | 658 cases; final-evaluation.completed.json; no final-test tuning |
| Shared-runtime integration | Complete | Six real calls, both roles, one loaded candidate; reset rejects late response; no plant writes |
| Portable candidate bundle | Complete | Local ZIP, weights hash and model card; no remote publication |
| Ollama alias registration/load | Blocked | CLI create and REST manifest reads return Access is denied; direct llama.cpp works |
| Replacement/release qualification | Failed | Telemetry quality regressions, insufficient independent/class support |
| Full-plant jitter/contention/resource comparison | Deferred | Not required to produce the standalone candidate; still required before promotion |

## Artifacts and exact SHA-256

All paths below are relative to the project root. The run is
`artifacts/optimization/candidates/qwen05b-observer-20261102-run04/`.

| Actual artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `artifacts/optimization/candidates/qwen05b-observer-20261102-run04/training/checkpoint-135/adapter_model.safetensors` | 35237104 | `f9840920f432882900328394421d8a5bce777e16b61c93e4ff3236e671527994` |
| `artifacts/optimization/candidates/qwen05b-observer-20261102-run04/export/merged-bf16/model.safetensors` | 988097824 | `fc7957e8d92a3f672a029614aeb0577123b2dd476cefac3405c855839604f666` |
| `artifacts/optimization/candidates/qwen05b-observer-20261102-run04/export/scram-observer-slm-f16.gguf` | 994156448 | `7f03bb9fbfc55fb757ad478701e86c7b3db1d60e6803f6217ddbbd13b9800db0` |
| `artifacts/optimization/candidates/qwen05b-observer-20261102-run04/export/scram-observer-slm-q4_k_m.gguf` | 397807520 | `96e995d3713c82a21e55009b8cdce825d37c1c495028d87b44704730c48d3149` |
| `artifacts/optimization/candidates/qwen05b-observer-20261102-run04/export/scram-observer-slm-q8_0.gguf` | 531067808 | `a977ee4348510fa1f62e06304fd67598e58131f9752ad0069f860fcb4807e5d3` |

Selected deployment copy:
`model/scram-observer-slm-candidate/scram-observer-slm-q8_0.gguf`, same Q8 hash.
Portable ZIP: `artifacts/optimization/candidates/qwen05b-observer-20261102-run04/distribution/scram-observer-slm-candidate.zip`
(505,984,540 bytes), SHA-256
`be5344548d9ebcf279a42c61041b7e5547659ee732004fae36aec94589c59133`.
The ZIP includes the new GGUF, model card, licenses, installers/runners and
per-class evaluation summaries. It excludes training caches and datasets.

## Working environment and training

Native Windows, RTX 3050 Laptop 6 GB, 16 GB RAM. Python 3.11.9 in `.venv-train`;
application Python was not replaced. Working versions: torch 2.11.0+cu128,
transformers 5.17.0, peft 0.21.2, accelerate 1.15.0, safetensors 0.8.0,
tokenizers 0.23.3, huggingface_hub 1.33.0. Exact freeze is saved in the run.
Unsloth/TRL were not used. WSL was unavailable and unnecessary.
Recorded resource check: 3,622,248,448 bytes available RAM, 5,406,457,856 bytes
free CUDA memory and 107,749,736,448 bytes free disk before the native experiment.
Peak main-training CUDA allocation 2,442,674,176 bytes; reserved 3,865,051,136.
These are actual training measurements, not application GPU savings.

LoRA rank 16/alpha 32/dropout 0, 8,798,208 trainable parameters, BF16, SDPA,
gradient checkpointing, microbatch 1/accumulation 8, AdamW 2e-4, seed 3407.
Budget two epochs/180 steps; validation every 45; fixed subset macro-F1 selects
checkpoint, unweighted assistant CE breaks ties; early stop after two failed
improvements. After weak step-45 recall, one recorded adjustment emphasized
class-label tokens (weight 10), preserving full assistant response/EOS training.
Run04 resumes actual run02 optimizer state. Selected checkpoint 135 macro-F1
0.6305659/unweighted loss 0.2432374; later step180 was not selected.
Phase B elapsed 3,729.06 seconds, including validation; this excludes phase A,
smoke and installation. Complete steps/config/RNG/recovery checkpoints are saved.

## Data and evaluation

Old preparation: 3,273 original training rows downsampled to 1,432; validation
1,094 unchanged. Twelve shared measured-rationale proposal trajectories crossed
historical train/validation; old proposal recording IDs were missing. Those
proposal rows were excluded from fitting/selection. New constructed proposal
histories use disjoint whole-recording groups. Historical telemetry inputs and
measured/commanded alignment remain intact; labels are historical scenario/
augmentation labels, not newly human-verified truth.

New data: 720 unique balanced training rows (90 for each task/class), validation
475 (66 proposals, 409 telemetry). Before balancing, deduplicated train pools had
2,354 rows; 258 train and 30 validation duplicate rows were removed. Exact counts,
recording IDs, target provenance and examples are in data-v1/manifest.json and
original-data-audit.json. Request IDs/recording IDs/fault metadata are outside
model inputs; application code retains authoritative request identity checks.
Full histories fit: max 2,241 training tokens including prompt and target.
Regeneration reproduced train/validation hashes exactly. Sealed test opened only
after freeze. Additional stress variants do not create independent gate support.

| Validation report | Proposal / telemetry cases | Macro-F1 | Invalid/timeout |
| --- | ---: | ---: | ---: |
| base-validation.json | 66 / 409 | 0.0824 | 342 |
| adapter-validation.json | 66 / 409 | 0.6499 | 0 |
| q4-validation.json | 42 / 48 | 0.5935 | 0 |
| f16-validation.json | 42 / 48 | 0.6165 | 0 |
| q8-validation.json | 42 / 48 | 0.6360 | 0 |
| legacy-validation.json | 42 / 48 | 0.6028 | 0 |

The first two rows use Transformers/batch 8; remaining rows use single-request
Ollama-bundled llama.cpp/CUDA. Their sample counts and speed are not interchangeable.
Fine-tuned full validation: proposals 66/66 correct, telemetry sensor recall
26/120, actuator 31/120, excursion 0/19, normal 121/150; 29/150 normal false alarms.

Same-runtime validation telemetry recalls:

| Report | Normal | Sensor | Actuator | Excursion | False alarms |
| --- | ---: | ---: | ---: | ---: | ---: |
| q4-validation.json | 83.3% | 16.7% | 8.3% | 0.0% | 2/12 |
| f16-validation.json | 91.7% | 16.7% | 16.7% | 0.0% | 1/12 |
| q8-validation.json | 83.3% | 16.7% | 33.3% | 0.0% | 2/12 |
| legacy-validation.json | 75.0% | 50.0% | 100.0% | 50.0% | 3/12 |

Q4 failed the predeclared maximum 0.02 macro-F1 loss against F16. Q8 passed that
quantization rule; this is not a release qualification. Full precision, recall,
confusion matrices, invalid counts, latency and raw responses are in each JSON
and deployment evaluation-per-class.csv. Difficult cases include missed 21.44%
command gaps and fabricated numeric explanations; see
[training/SLM_CANDIDATE_FAILURES.md](training/SLM_CANDIDATE_FAILURES.md).

**Frozen final results:** 147/147 constructed proposal cases correct, with 27
normal and 24 provenance examples (below the gate's 30/class requirement).
Primary historical telemetry: 471 cases; sensor recall 3/120 (2.5%), actuator
6/120 (5%), excursion 114/120 (95%), normal 65/111 (58.6%); 46/111 false alarms,
zero invalid outputs. Frozen-level and dropout stress each had 0/20 correct
sensor classifications. No weights/thresholds were changed using these results.

SLM-only, CPU-only and combined telemetry are separate. Across all 511 telemetry
cases including stress, SLM fault-classification errors were 277/400, CPU and
combined 44/400. CPU/combined false alarms were 0/111; SLM 46/111. Current runtime
uses the approved CPU result as primary, so combined equals CPU here. That does
not repair the SLM or establish replacement readiness. Scores are uncalibrated.

## Serving, verification and next action

Export tools: llama.cpp build11607/ff026f8c0. Inference: existing Ollama-bundled
llama.cpp 0.8.0-dev/39febd2ad, CUDA v12. All 25 layers offloaded. GGUF BOS/EOS,
chat template and input token IDs were verified against training; no context
truncation. Both tasks share one context setting (3072) and alias
`scram-observer-slm-candidate`, with separate histories/routing/freshness checks.
Telemetry remains advisory and proposal authority boundaries are unchanged.
Legacy/operator weight hashes match the original manifest; CPU/control/governance/
signing/interlock code was not changed by this SLM work.

Code verification: 314 tests passed, 2 skipped, one existing deprecation warning;
Ruff and git diff whitespace checks passed. These are not model-quality gates.
The portable artifact was reloaded in a fresh direct server; both role responses,
ZIP CRC and all member hashes passed (portable-deployment-verification.json). Task-owned inference
processes are shut down for the user's pause; no unrelated apps were stopped.

Read [training/SHARED_SLM_RECIPE.md](training/SHARED_SLM_RECIPE.md) for prepare,
train, resume, evaluate and export commands. Candidate-only launch:
`python scripts/run_observer_slm.py` (port11438, never starts the plant).
Ollama-specific blocker, errors and attempts are in `ollama-blocker.json`.
Normal-user follow-up, with ordinary Ollama running:
`python scripts/install_observer_slm.py`. That Ollama installation is unverified;
it registers a new alias and must not be treated as approval to promote it.

**Next work is improving telemetry data/supervision and a new versioned 0.5B
experiment with an independent holdout. Do not resume training into this frozen
run, reuse its final test for tuning, delete V2/V3, or switch the default yet.**
