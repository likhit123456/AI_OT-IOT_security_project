# Reproduce the native shared-observer experiment

Use Python 3.11 in a separate environment. The tested environment is `.venv-train`;
its exact freeze is saved in each candidate directory. The application Python and
its requirements were not replaced. Working core versions: torch 2.11.0+cu128,
transformers 5.17.0, peft 0.21.2, accelerate 1.15.0, safetensors 0.8.0,
tokenizers 0.23.3, huggingface_hub 1.33.0. Unsloth/TRL are not used by this backend.
The complete package freeze, rather than a claim that a fresh minimal install was
tested, is the environment record. PyTorch's [native installation guidance](https://pytorch.org/get-started/locally/)
and [PEFT LoRA documentation](https://huggingface.co/docs/peft/developer_guides/lora)
are the upstream references; actual CUDA forward/backward tests verify this machine.

Run from the project root. Use new directories for reproduction; never replace
the original source datasets, approved CPU artifacts or saved historical reports.

```powershell
# Official trainable checkpoint; downloads only, no uploads.
.\.venv-train\Scripts\python.exe scripts/fetch_observer_slm_base.py

# Audited data: original V2 proposal rows excluded, new variants grouped by source.
.\.venv-train\Scripts\python.exe -m training.train_shared_observer --stage prepare-native --destination artifacts/optimization/candidates/reproduce/data-v1

# Real CUDA/PEFT diagnostics, exact tokenizer audit and 16-step task smoke.
.\.venv-train\Scripts\python.exe -m training.shared_slm_native preflight --run artifacts/optimization/candidates/reproduce
.\.venv-train\Scripts\python.exe -m training.shared_slm_native tokens --run artifacts/optimization/candidates/reproduce
.\.venv-train\Scripts\python.exe -m training.shared_slm_native smoke --run artifacts/optimization/candidates/reproduce
.\.venv-train\Scripts\python.exe -m training.shared_slm_native reload-smoke --run artifacts/optimization/candidates/reproduce

# Phase A. The LR schedule has the full 180-step/two-epoch horizon; stop after
# the complete step-45 checkpoint, reproducing the initial diagnostic checkpoint.
.\.venv-train\Scripts\python.exe -m training.shared_slm_native train --run artifacts/optimization/candidates/reproduce/phase-a --data artifacts/optimization/candidates/reproduce/data-v1 --stop-after-step 45

# Phase B: one documented adjustment following poor class recall, not a parameter
# sweep. Resume the actual optimizer state, emphasize class spans, validate in batches.
.\.venv-train\Scripts\python.exe -m training.shared_slm_native train --run artifacts/optimization/candidates/reproduce/phase-b --data artifacts/optimization/candidates/reproduce/data-v1 --resume artifacts/optimization/candidates/reproduce/phase-a/training/checkpoint-45 --validation-batch-size 4 --label-loss-weight 10
```

The recorded run additionally saved a batch-four validation-only baseline between
the phases and supplied it with `--initial-validation-report`. Omitting that flag
recomputes the same fixed subset at resume. It does not add optimizer steps.
Class-token weighting changes the training objective only; the selection loss
remains unweighted assistant-only CE. The first phase's loss and weighted phase's
loss must not be drawn as an uninterrupted comparable curve.

To resume an interrupted phase, use its same `--run`, data and learning options,
plus `--resume` pointing to the last complete recovery checkpoint from
`training/recovery.json`. For the recorded weighted run, also pass the original
`--initial-validation-report` value so the recorded configuration stays identical.
Never launch a second trainer against an output directory already being written.
Checkpoint optimizer files are trusted local PyTorch files, not public downloads.

The official base is pinned to revision
`7ae557604adf67be50417f59c2c2f167def9a775`. Its safetensors SHA-256 is
`fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe`.
Tokenizer files, template, license, code/data hashes and seeds are retained.
CUDA/batching can change numerical results; byte-identical training is not claimed.

## Evaluate and export the selected checkpoint

`training/slm_artifact_tools.py` defaults to the recorded run04 and its data path.
For another build, pass `--run <phase-b>` and `--data <data-v1>` explicitly.
Outputs refuse to overwrite existing evaluation reports.

```powershell
# Identical validation inputs/generation for the base and saved candidate.
# --per-class 0 means the entire validation set; --batch-size controls inference only.
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools hf-eval --base-only --per-class 0 --batch-size 8 --output artifacts/optimization/candidates/qwen05b-observer-20261102-run04/base-validation-new.json
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools hf-eval --per-class 0 --batch-size 8 --output artifacts/optimization/candidates/qwen05b-observer-20261102-run04/adapter-validation-new.json

# CPU merge -> preserved BF16 safetensors -> F16 GGUF -> nominal Q4_K_M GGUF.
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools export
```

Export uses the existing `.tools/llama.cpp/convert_hf_to_gguf.py`, its local
conversion/gguf-py packages and `bin/llama-quantize.exe`. The tested binary reports
build 11607, commit ff026f8c0, Clang 20.1.8 for Windows x86_64. Exact file hashes
and complete conversion/quantization logs are saved under `export/`. This records
the working local tooling; the tools are not bundled into the small deployment.
Qwen's tensor dimensions cause fallback quantization for many tensors: inspect
the actual types/size, not just the nominal Q4_K_M name.

An isolated Ollama 0.40.13 was started on `127.0.0.1:11435`, with a workspace-local
model store, max-loaded-models one, parallelism one, cloud disabled and process-local
`LLAMA_ARG_CACHE_RAM=0`. Its process record is in run02/ollama. Those environment
settings apply to that child process only; global services/settings were not changed.
Its blob/create REST calls succeeded, but manifest reads (`/api/show`, `/api/tags`)
failed with `Access is denied`; CLI `ollama create` also failed. Relative-path and
isolated legacy-layout manifest checks did not fix it. No ACL, service or global
environment changes were made. Ollama registration remains blocked in this agent.

The working fallback uses the **existing Ollama-bundled llama.cpp server** at
`%LOCALAPPDATA%/Programs/Ollama/lib/ollama/llama-server.exe`, version 0.8.0-dev,
commit 39febd2ad, Clang 18.1.8. `training/local_gguf_server.py` starts only its own
recorded process on port 11437, one model/slot, fixed context, `--device CUDA0`
and `-ngl 99`. Its child-only PATH includes `cuda_v12`; `GGML_BACKEND_PATH` points
to the **ggml-cuda.dll file**, not its directory. CUDA discovery, 25/25 layer
offload, buffers, binary/backend hashes and real timings are recorded. Initial
CPU setup attempts and an interrupted validation are preserved separately.

Set `$env:SCRAM_SLM_EVAL_BACKEND='llama_cpp'` (the recorded build default).
`register-local` writes an experiment-only alias registry; `local-eval` loads
the real GGUF and calls llama.cpp. The old `register-ollama`/`ollama-eval` stage
names are compatibility aliases and obey the same backend variable. To retry
Ollama after resolving its environment permissions, explicitly set it to `ollama`
and start the isolated Ollama server on 11435. Do not compare timing across
different backends as if it measured only model quality.

```powershell
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools register-local
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools local-eval --output artifacts/optimization/candidates/qwen05b-observer-20261102-run04/q4-validation-new.json

# Explicit comparison aliases in the isolated store; original files/aliases retained.
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools register-local --legacy
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools local-eval --legacy --output artifacts/optimization/candidates/qwen05b-observer-20261102-run04/legacy-validation-new.json

# Same-runtime F16 comparison; choose quantization on validation, never on test.
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools register-local --model artifacts/optimization/candidates/qwen05b-observer-20261102-run04/export/scram-observer-slm-f16.gguf --alias scram-observer-slm-f16-eval
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools local-eval --model artifacts/optimization/candidates/qwen05b-observer-20261102-run04/export/scram-observer-slm-f16.gguf --alias scram-observer-slm-f16-eval --output artifacts/optimization/candidates/qwen05b-observer-20261102-run04/f16-validation-new.json
```

The quantization-selection rule is declared in `experiments/slm_quantization_selection.json`.
If Q4 fails it, export/evaluate Q8_0 once with `export --quant-type Q8_0`; preserve
both. The selected alias must point to the chosen artifact before the final test.

```powershell
# Only AFTER selection/configuration is frozen. This opens the sealed test once,
# constructs documented additional held-out proposal/stress cases and hashes them.
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools freeze
.\.venv-train\Scripts\python.exe -m training.slm_artifact_tools local-eval --final --output artifacts/optimization/candidates/qwen05b-observer-20261102-run04/final-test.json

# Genuine inference through the existing bounded shared runtime; no plant writes.
.\.venv-train\Scripts\python.exe -m training.verify_slm_runtime
```

Synthetic frozen-level/dropout stress variants are reported separately and do not
increase independent-recording/class support for release gates. CPU-only and
combined telemetry results remain separate from the SLM's own predictions.
The original clean test file is preserved byte-for-byte. Do not rerun final-test
selection loops or tune against the final results.

## Candidate-only serving and rollback

After packaging, `python scripts/run_observer_slm.py` verifies the deployment
weight hash and starts the direct server on port 11438, without starting SCRAM.
Set `SCRAM_OBSERVER_MODE=unified`, `SCRAM_OBSERVER_MODEL=scram-observer-slm-candidate`,
`SCRAM_OBSERVER_SCHEMA=slm-v1`, `SCRAM_OBSERVER_CONTEXT=3072`,
`SCRAM_OBSERVER_BACKEND=llama_cpp` and `SCRAM_OBSERVER_URL=http://127.0.0.1:11438`
for an opt-in app session. Default legacy is retained because quality gates fail.
Rollback uses `SCRAM_OBSERVER_MODE=legacy` and clears model/schema/context/backend/URL
overrides. Do not delete the specialists. The separate Ollama installer is
`python scripts/install_observer_slm.py`; run in normal PowerShell after starting
Ollama if its manifest access remains blocked in the agent. This installation
is not claimed verified here.
