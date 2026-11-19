# SCRAM models

Run all commands below from the repository root. The control room can run with
`SCRAM_OBSERVER_MODE=none` with `python -m live --brain baseline` without LLM weights or Ollama.

## What is included

| Directory | Purpose |
| --- | --- |
| `scram-operator-gguf/` | Ollama definition for supervisory setpoint generation |
| `scram-operator-lora/` | Operator adapter configuration, tokenizer and model card |
| `scram-shared-observer/` | Shared F16 demo observer, deployment manifest and development evidence |
| `scram-observer-gguf/`, `scram-observer-v3-gguf/` | Historical metadata and evaluation evidence; V2/V3 model weights are retired |
| `telemetry-physics/` | Active advisory CPU detector, release policy and evaluation evidence |
| `telemetry-classifier/` | Earlier restricted CPU classifier, used as a fallback |

GGUF weights, adapter weights and training checkpoints are ignored by Git. A
fresh clone includes their metadata, not runnable LLM weights. The CPU JSON
models are included and need no GPU or scikit-learn at runtime.

## Register local LLM weights

Obtain the matching trained files from the project maintainer or regenerate
them using the training sources below. This repository does not provide a
public weights download. Place each file at the path in this table:

| Ollama name | Required local weight file |
| --- | --- |
| `scram-operator` | `model/scram-operator-gguf/scram-operator-q4_k_m.gguf` |

With Ollama installed and running:

```sh
ollama create scram-operator -f model/scram-operator-gguf/Modelfile
ollama list
```

The default shared observer uses `model/scram-shared-observer/app-deployment/scram-observer-demo-f16.gguf`,
SHA-256 `f155e25ce8898a332c11191d70f36a308cf12a8d14fce6743f11c792959f561d`.
The weight file is ignored by Git and must be supplied for a fresh clone. The
launcher verifies its hash and fixed development report, then manages the local
llama.cpp runner at port 11437. The current Windows setup expects Ollama's bundled
CUDA llama-server at `%LOCALAPPDATA%/Programs/Ollama/lib/ollama/llama-server.exe`.
Missing weights, a missing runner, or an occupied port cause startup to fail
explicitly. The combined demo observer has not passed an independent accuracy test.

The manual studio additionally uses the stock `qwen2.5:1.5b` parser model
(`ollama pull qwen2.5:1.5b`). Manual buttons work without it.

The live operator falls back to deterministic baseline control when unavailable.
The active live runtime no longer selects V2/V3 observers. It runs the
approved CPU detector independently on its own measured window, even with no
LLM or malformed LLM output. CPU and LLM evidence are reported separately.
Its pending evaluation is documented in [current status](../CURRENT_STATUS.md).

## Training and evaluation

The operator pipeline is [`training/train_local.py`](../training/train_local.py),
using `training/train.jsonl` and `training/val.jsonl`. Its shared training/serving
format lives in [`l3_brain/prompt_format.py`](../l3_brain/prompt_format.py).
Training requires the separate [training dependencies](../requirements-train.txt),
a compatible GPU environment, and local llama.cpp conversion/quantization tools
under `.tools/llama.cpp` for export. The script documents its stages and paths;
training/export can replace local operator outputs.

The [observer notebook](../training/scram_observer_finetune.ipynb) now defaults to
`training/observer_clean/` and exports a separate candidate. The deployed v3
metadata and historical evaluations refer to the earlier datasets. This notebook
is not a promise of byte-identical reconstruction of historical v2/v3 weights.

See the [operator card](scram-operator-lora/README.md), the historical
[v3 card](scram-observer-v3-gguf/lora/README.md), and
[physics detector](telemetry-physics/README.md) for scope and evidence.

## Local Ollama memory configuration

The project recorded memory pressure while serving three models concurrently.
Its local workaround is `LLAMA_ARG_CACHE_RAM=0`; observed memory usage is specific
to that machine and Ollama build, not a universal requirement or guarantee.
`python -m live --configure-ollama` explicitly applies the project's workaround
and may restart Ollama. Normal startup does not change these settings.

## Evidence integrity

Release policies verify SHA-256 hashes before loading approved CPU models.
Git attributes preserve JSON/JSONL bytes across checkouts. Do not reformat frozen
evidence or edit approval hashes to bypass a mismatch. A changed model needs new
validation and release evidence. These models support a simulation; their
evaluations do not certify physical plant safety.
