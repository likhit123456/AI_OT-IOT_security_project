# Evidence-v2 execution status

**Latest automation update:** see [SLM_AUTOMATION.md](SLM_AUTOMATION.md). The
single launcher now handles candidate collection through static quality
qualification and Ollama verification without prompts. Its actual sandbox
execution stopped at Docker permission denial. 81 focused tests pass, including
the full collector pause regression. No new completed recording/training is
claimed. Earlier status detail follows.

Updated 2026-11-08 after the user authorized collection, training and qualification.

**Ordinary Ollama import and real two-role inference are now verified.** Fresh
data collection needs a normal-user PowerShell command because this sandbox is
denied access to Docker's named pipes. The first native simulator smoke failed
on a collector timing check; that bug is fixed. No completed recording or new
trained model exists yet. V2/V3 remain defaults and run04 remains frozen.

## Verified Ollama loading

The normal server at `127.0.0.1:11434` reports version 0.40.15. The supported
HTTP blob/create route imported the exact frozen Q8 GGUF, SHA-256
`a977ee4348510fa1f62e06304fd67598e58131f9752ad0069f860fcb4807e5d3`,
as `scram-observer-slm-candidate`. `/api/show` established the same weight blob.
Both proposal and telemetry calls generated parseable role-correct verdicts.
The candidate was unloaded afterward; all other alias digests were preserved.

Evidence:
`artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/ollama-http-verification.json`.
Reproduction: [verify_observer_ollama.py](../scripts/verify_observer_ollama.py).
Protocol reference: [Ollama's official API](https://github.com/ollama/ollama/blob/main/docs/api.md).
This clears a loading gate, not the frozen model's failed quality gates. The
original CLI import remains permission-sensitive; the working route uses HTTP.

## Collection and first smoke

The user started Docker Desktop. Both `docker_engine` and
`dockerDesktopLinuxEngine` now exist but return permission denied to the sandbox.
No ACL, global setting or Docker API exposure was changed.

The normal-user collector did reach the image and verify its native dropout
interface. The first five samples were preserved before an abort on a scheduling
pause. The container was isolated on loopback port 59882 and cleaned up; the
application plant was not contacted. Initial image ID:
`sha256:c4504ddab41e7dc9b5b43df598a81d51129ca824bc5a0044af51545b91eca378`;
binary SHA-256:
`f7f50fbc05ead9f73cadfd2ed214c5e50a881d5794220211e6916a90f393981a`.

The collector now records the timestamp after the state/command transaction,
skips missed sample deadlines instead of burst replay or abort, and leaves
misaligned windows for the builder to quarantine. It also checks native stuck
actuator behavior before collection. A fixed valve-command excitation was
declared before any completed recording or optimization, so stuck valves receive
an observable challenge. The earlier plan is retained as
`collection-plan-before-native-stuck-probe.json`; failed partial data is excluded.

Run from normal PowerShell:

```powershell
cd C:\Users\likhi\Downloads\SCRAM-Project
python -m training.collect_slm_evidence collect --only train-normal-00
```

After inspecting a successful smoke's timing, native probe results and measured
trace, the complete predeclared collection is:

```powershell
python -m training.collect_slm_evidence collect --workers 4
```

The plan contains 64 independent recordings of 210 seconds each, with disjoint
train/validation/sealed groups. Sequential recording time is about 3.73 hours;
four workers have a theoretical recording-time minimum of 56 minutes plus
startup/cleanup overhead. Actual duration has not been measured. Completed files
are checksum-verified and skipped on resume. Duplicate writers cannot acquire the
same recording lock. Ctrl+C/failure cancels queued work and active workers clean
up only containers whose IDs and experiment ownership labels both match.

Every container starts a fresh native simulator with a private loopback port.
The collector bypasses the image's Modbus/visualization entrypoint. It records
actual requested controller commands with aligned measured states; interlocked
effective commands are marked missing, never copied from actual valve positions.
Calibration and fault probes affect only the new container. Per-recording code,
image and binary hashes are retained. No compose restart or default plant port
is used. Failed attempts retain their partial trace and error report.

## Prepared training and serving path

[slm_evidence_data.py](slm_evidence_data.py) prepares only train and validation.
It excludes misaligned/overridden-command windows, unobservable native faults,
and confounded single-fault labels. Excluded windows enter a quarantine queue.
Targets cite measured arithmetic; simulator truth is restricted to label-quality
screening outside model input. These remain native-intervention construction
labels, not human gold. The old ambiguous historical supervision is not reused.
Human review is still required before retaining ambiguous cases.

Proposal cases use newly recorded measured states with explicit, attributable
transformations. Their limited language templates are reported separately from
native telemetry; they do not establish independent operator-language coverage.

The builder enforces unique identities, whole-recording separation, balanced
training caps and minimum support. Insufficient cases/groups remain readiness
failures. It does not open sealed raw recordings.

```powershell
python -m training.slm_evidence_data
.venv-train/Scripts/python.exe -m training.shared_slm_native tokens --run artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2 --data artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/data-v2
.venv-train/Scripts/python.exe -m training.shared_slm_native smoke --run artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2 --data artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/data-v2 --policy experiments/slm_evidence_v2_policy.json --label-loss-weight 10
.venv-train/Scripts/python.exe -m training.shared_slm_native reload-smoke --run artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2 --data artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/data-v2
.venv-train/Scripts/python.exe -m training.shared_slm_native train --run artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2 --data artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/data-v2 --policy experiments/slm_evidence_v2_policy.json --label-loss-weight 10 --validation-batch-size 4
```

The policy-aware trainer checks provenance, representation, support and dataset
hashes before loading weights. It uses seed 3408 and full fixed validation at
each scheduled checkpoint. It enforces the 180-step/two-epoch cap, context budget,
class weight, fresh initialization and own-run-only recovery. A completed run
cannot silently restart. Old run04 training defaults and its frozen outputs
remain distinct.

Serving now supports explicit `SCRAM_OBSERVER_SCHEMA=measured-evidence-v2` in
unified mode, with an explicitly supplied new model alias. The prompt is built
by the identical helper used in data preparation. Authoritative request identity
stays outside model generation. No default or environment override was activated.

## Evaluation and qualification still required

After successful real training/reload, use the existing artifact tools with
explicit **new run and data paths**, full validation (`--per-class 0`), and new
aliases. Compare base, adapter, F16/Q4/Q8 and unchanged V2/V3 fairly. Never invoke
the historical `freeze` stage for this dataset: it now rejects evidence-v2.

[freeze_evidence_candidate.py](freeze_evidence_candidate.py) supplies the new
`select-quantization` and `freeze` stages. Quantization selection recomputes
metrics from the same full new validation cases and applies the existing
predeclared degradation rules. Freeze requires completed policy-aware training,
the selected adapter/export identities and fixed allocation/data/policy hashes.
Only then can it open the new sealed recordings. It rejects overlap and reports
support shortfalls without padding or changing splits. This code has guard tests
but has not been exercised with a newly trained/exported candidate.

Actual final quality, independent operator-language coverage, explicit
frozen-level/dropout stress comparisons, sustained plant freshness/jitter, GPU
contention, resident memory/reload behavior and rollback qualification remain
unmeasured for a new candidate. No result is inferred from passing code tests.
All release gates must pass before any default or specialist-download change.

## Checks completed

76 focused tests passed; Ruff passed for changed Python files. Tests cover
container ownership/private-port checks, cancellation/duplicate-writer cleanup,
timing pauses, native-data screening, hidden truth exclusion, support failures,
training/serving parity, external request identity, quantization class regressions
and refusal to open old/new test data through an inappropriate path.

CUDA was available. Available system RAM fell below 1 GB after Docker startup;
recheck host/GPU memory before real model loading. No unrelated app was stopped.
