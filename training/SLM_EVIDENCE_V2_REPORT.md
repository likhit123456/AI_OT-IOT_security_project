# Observer evidence v2: continuation on 2026-11-08

**Later execution update:** ordinary Ollama loading is now verified through HTTP.
Fresh collection/tooling and its current blockers are described in
[SLM_EVIDENCE_V2_EXECUTION.md](SLM_EVIDENCE_V2_EXECUTION.md). The audit below is the
earlier preparation record; its original Docker/Ollama blocker descriptions are
historical. No improved model has yet been trained.

The next experiment now has a measured-only representation, a reproducible
supervision audit, conservative source-window matching and a declared training
policy. **No new training occurred and no replacement is qualified.** The
completed run04, its sealed final evaluation and the deployed defaults are intact.

Experiment directory:
`artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/`.
Policy: [slm_evidence_v2_policy.json](../experiments/slm_evidence_v2_policy.json).

## What the saved train/validation data establishes

Only the existing 720 training and 475 validation examples were audited. The
769 telemetry windows contain 360 training and 409 validation examples. No old
final-test file was opened and no labels or target explanations were rewritten.

| Review finding | Train | Validation |
| --- | ---: | ---: |
| Missing exact historical per-example recording ID | 360 | 409 |
| Template causal assertion needing review | 67 | 96 |
| Claimed frozen duration exceeds visible constant suffix | 7 | 4 |

These are review flags, not proof that the historical class label is wrong.
The frozen-duration flag compares the claim with `(constant_suffix_samples-1)`
sample intervals. A longer freeze may have existed outside the visible window;
the input cannot establish that duration. No target gap/range/delta discrepancies
were flagged by the limited construction-template checks; this is not a general
natural-language claim verifier.

Conservative matching indexed 5,632 windows from **only already assigned
train/validation source recordings**. Each match requires identical rounded
measured histories except at most one sensor channel; commands are omitted to
accommodate explicitly constructed command variants. Actual valve positions,
the other sensor channels, duration and scram history must match.

- 767/769 examples match one candidate recording group; two match multiple groups.
- 765/769 match only source recordings lacking independently recorded commands.
  Four match sources with recorded commands.
- All four validation classes trace to at most two candidate recording groups;
  validation excursion traces to one.

A unique candidate is useful evidence for review, not authoritative lineage.
Source command availability also does not establish whether a later command
variant was deliberately constructed. These matches must not be counted as new
independent recordings or silently substituted into historical manifests.

Authoritative outputs are `supervision-audit-verified/report.json`, its
`review-queue.jsonl`, and `source-recovery-verified/report.json` plus
`source-candidates.jsonl`. The initial `supervision-audit/report.json` has invalid
token counts from counting tokenizer mapping keys; it is superseded. The verified
audit renders the pinned chat template then counts encoded token IDs explicitly.
The initial `source-recovery/` is superseded by `source-recovery-verified/`, rerun
after the final source formatting change. Prior outputs were retained.

## Measured-only representation

[telemetry_evidence.py](../monitor/telemetry_evidence.py) computes per-channel
range, signed endpoint delta, maximum adjacent change, zero count and longest
equal run, plus signed final and maximum absolute actual-minus-command valve
gaps. Gaps use percentage points. Counts refer to rounded wire samples, not
elapsed seconds. The function rejects misaligned windows, missing/extra fields,
non-finite arithmetic, numeric booleans and hidden metadata. It emits no labels,
fault thresholds, truth-channel values or recording IDs.

The experimental prompt keeps every raw history sample. Its helper is reusable
for a future explicitly selected serving schema, but **is not wired into the
deployed runtime** or the frozen slm-v1 candidate. Proposal prompts stay slm-v1.

Pinned local tokenizer results over all 1,195 audited examples:

| Budget measurement | Train maximum | Validation maximum |
| --- | ---: | ---: |
| Prompt tokens including generation prefix | 2,529 | 2,525 |
| Complete sequence with original assistant target | 2,588 | 2,579 |
| Prompts exceeding 3,072 with 192 generated tokens reserved | 0 | 0 |

This proves representation fit on these inputs, not improved model quality.
There has been no v2 model inference, training or new final evaluation.

## Declared next run and remaining prerequisites

The policy fixes the same pinned 0.5B base with a fresh adapter/optimizer,
seed 3408, at most one 16-step smoke and one two-epoch/180-step main run,
class-token weight 10 from the outset, validation every 45 steps and selection
by eight-task/class macro-F1 with unweighted assistant CE as tie-breaker.
Two unsuccessful validation checkpoints stop training. No objective changes
mid-run, resume from run04 or use of the old final test are permitted.

Training readiness requires reviewed labels/reasons and a new independent
recording manifest. Exact command provenance and source/time lineage must be
retained per example. Allocate all children of a recording to one split before
building examples. Require at least 30 cases per task/class in validation and
new final test, spanning at least three recordings per class; report constructed
variants separately. Existing release gates still apply.

Fresh simulator access is unavailable in this agent: `wsl -l -q` returned
`Wsl/EnumerateDistros/Service/E_ACCESSDENIED`; `docker compose ps --format json`
could not connect to the Docker engine pipe. No simulator was started or reset.
The stock collection helpers reset the application's simulator, so they were
not invoked. Collection should use a separately launched simulator on a private
port and its own output files, with the corrected simulator build hash recorded.

Normal Ollama at `127.0.0.1:11434` returns its existing model list. Retrying
`python scripts/install_observer_slm.py` again failed with `Error: Access is
denied.` Candidate registration/load remains unverified. `/api/ps` returned an
empty model list afterward. No service, ACL or global environment was changed.

## Verification and reproduction

54 focused tests passed, including arithmetic, malformed/non-finite inputs,
hidden-metadata exclusion, token counting, whole-window retention, source-group
allowlisting and existing shared-runtime contract tests. Ruff passed for all new
Python files. Git whitespace checks passed with existing line-ending warnings.

Run from the project root, with a **new unused output directory** each time:

```powershell
.venv-train/Scripts/python.exe -m training.audit_slm_evidence --tokens --destination artifacts/optimization/candidates/your-new-audit
python -m training.recover_slm_provenance --queue artifacts/optimization/candidates/your-new-audit/review-queue.jsonl --destination artifacts/optimization/candidates/your-new-recovery
python -m pytest -q -p no:cacheprovider --basetemp artifacts/pytest-your-new-run tests/test_telemetry_evidence.py tests/test_shared_slm_contract.py tests/test_observer_runtime.py
python -m ruff check monitor/telemetry_evidence.py training/audit_slm_evidence.py training/recover_slm_provenance.py tests/test_telemetry_evidence.py
```

Next work is new isolated recording collection and label review, then building
versioned supervised data and running the declared experiment. Adding summaries
to the old candidate's prompt alone is not evidence of a trained improvement.
