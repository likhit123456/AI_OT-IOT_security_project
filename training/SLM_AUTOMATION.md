# Run the evidence-v2 candidate experiment

**Latest recovery:** 53 original recordings completed successfully. The
forbidden-port allocation and journal resume are repaired; see
[SLM_PORT_RECOVERY.md](SLM_PORT_RECOVERY.md). A separate 16-recording native
excursion supplement addresses the train/validation readiness shortfall before
any training. The original plan/recordings are preserved. The same launcher
resumes; 87 focused tests pass. Earlier timing estimates below describe the
initial 64-case plan and exclude the supplement.

The collector scheduling failure from the first attempt is fixed. An end-to-end
collector test now introduces the same kind of pause, completes the recording,
preserves the actual timing gap and verifies that the affected window is
quarantined. Current focused checks: 81 passed; Ruff passed.

The launcher automatically selects the project directory, so it also fixes the
`No module named training` error when invoked from the home or Downloads folder.
It has no confirmation or permission prompts.

Run this single command in **normal PowerShell**:

```powershell
& 'C:\Users\likhi\Downloads\SCRAM-Project\RUN_SLM_EVIDENCE.cmd'
```

It runs Docker preflight, a real smoke recording and its inspection, the full
predeclared independent collection, screened data preparation, CUDA/token checks,
fresh model smoke/save/reload, policy-aware main training, base/adapter and
GGUF/specialist validation, fixed quantization selection, model freeze, new held-out
and stress evaluation, static quality qualification, a portable experimental
bundle, and ordinary Ollama two-role loading under a new alias.

The default is two isolated collection workers. Recording time alone has an ideal
minimum of 112 minutes at two workers, plus container overhead. Training and
inference comparisons take additional time. Four workers can be selected with
`--workers 4`; that reduces the ideal recording-time minimum to 56 minutes but
does not establish actual throughput on this machine.

All containers use private loopback ports; the application simulator is neither
contacted nor reset. Existing weights remain preserved. Training requires adequate
unique cases, recording groups and exact command provenance. Ambiguous data is
quarantined. If support is insufficient, the pipeline stops with the evidence
instead of duplicating examples or relabelling them to force readiness.

Progress and exact failures are retained at:

`artifacts/optimization/candidates/qwen05b-observer-20261108-evidence-v2/automation/status.json`

Each subprocess has its own attempt log in that directory. Completed stages are
reused only when their recorded inputs/outputs still match their hashes. Main
training may resume only its own saved recovery checkpoint. Interrupted actual
GGUF evaluations may resume the identical case order and model identity. Final
candidate and specialist comparisons each have their own one-run marker. The
historical final test is never opened. Failed Ollama checks are preserved before
retrying the same candidate.

The collector prints its actual code hash and timing policy before accessing
Docker. The obsolete `collection fell behind its fixed sample schedule` abort is
absent from the current code. The previous partial recording remains preserved.

**Current execution blocker:** Windows denies this sandbox access to Docker's
named pipes. The automatic launcher was actually attempted here and failed at
Docker preflight; no new data or training was claimed. The user's normal shell
already reached Docker 29.6.2. Starting the same launcher in that shell supplies
the required execution context; no ACL changes, exposed Docker API, privilege
proxy, or service elevation is used.

**This is candidate-build automation, not automatic release approval.** Static
quality failure is reported honestly. Even when static quality passes, sustained
freshness/jitter, GPU contention, memory/reload behavior and rollback release
qualification remain required. The pipeline records those runtime gates as
unmeasured and does not change V2/V3 defaults or remove specialist downloads.
Exit zero means the requested pipeline commands completed; consult
`qualification.json` and `automation/status.json` for actual qualification status.
