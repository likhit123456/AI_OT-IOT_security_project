# Collection recovery after 53 completed recordings

The normal-user run completed 53 of the original 64 recordings. Docker then
selected loopback host port 55555 for a new task-owned container. The collector
rejected it before creating a plant client, preserved the failure and cleaned up.
The other in-progress recording was cancelled. None of the 53 completed files
was lost or changed.

The corrected allocator removes owned containers whose allocated host port is
forbidden, then requests a new allocation, with at most eight attempts. Ownership
checks and the prohibition on connecting to the application's port remain intact.
Regression tests cover successful retry, bounded exhaustion, cleanup, and refusal
to create any plant client while resolving rejected allocations.

All 53 completed raw SHA-256 identities and metadata/spec/plan identities were
verified. The original collection plan remains byte-identical at SHA-256
`2092124e827b1bc86ea2dff381de900bc3114ab7e9c5ee1aa7bb482b2aebae78`.
The pipeline journal was repaired specifically for the known earlier collector
revision, preserving its prior identities and a complete status backup. It
accepts no arbitrary code changes or corrupted recordings and cannot apply this
repair after model preparation/training. The audit record is under
`automation/collector-fix-*.json`; the actual state is `ready_to_resume_collection`.
Sealed raw bytes were hashed for integrity, never decoded or inspected as
measurements. No sealed results informed these changes.

## Additional readiness issue caught before training

A separate diagnostic build used only the completed train/validation recordings.
It found one qualifying training excursion and zero qualifying validation
excursions. The original fixture settled around 2820 kPa, below the unchanged
2850 kPa criterion. The diagnostic dataset is explicitly not training-ready and
is retained under `readiness-preview-after-port-stop/`. No model was trained on
it and no historical or new final test was opened.

The initial 64-recording plan was preserved. A **separate, pre-training**
`excursion-supplement-plan.json` allocates 16 new recording identities: eight train,
four validation and four sealed. Each is a fresh 330-second native process run.
Its declared pressure fixture uses measured feedback to vent a high-pressure
plateau with targets between 2865 and 2900 kPa and a measured level trim. It uses
the production `PlantService` and SIS; no production controller/interlock code
was changed. Labels, observability requirements and class/recording support
thresholds were not relaxed.

The launcher first runs one supplemental training smoke and requires at least
three usable native excursion windows before collecting the supplement. This
actual smoke has not yet run in the sandbox, which still lacks Docker access.
Its simulator behavior and eventual class support therefore remain to be measured.
The builder will still refuse training if support is insufficient.

Data preparation, policy validation and the new final freeze now retain/check
both independent source-plan hashes. Duplicate recording identities across plans
are rejected. Completed initial recordings retain their original source-plan
identity; they are never relabelled or rewritten. The final data builder reads
sealed measurements only after the candidate and both allocation plans freeze.

## Resume

From normal PowerShell, use the same launcher:

```powershell
& 'C:\Users\likhi\Downloads\SCRAM-Project\RUN_SLM_EVIDENCE.cmd'
```

It reuses the 53 completed recordings, verifies the supplemental fixture, finishes
the 11 missing original recordings, collects the independently allocated
supplement, then proceeds to preparation/training only if all readiness checks
pass. Added recordings take additional collection time. It does not restart the
53 completed cases or ask for confirmation between stages.

87 focused tests and Ruff passed after these fixes. They verify code behavior,
not model quality or native fixture performance. New training, evaluation and
release qualification remain unfinished. The frozen old candidate and V2/V3
weights/defaults remain preserved.
