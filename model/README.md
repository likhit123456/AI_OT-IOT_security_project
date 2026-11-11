# SCRAM models

Run all commands below from the repository root. The control room can run with
`python -m live --brain baseline` without model weights or Ollama.

## What is included

| Directory | Purpose |
| --- | --- |
| `scram-operator-gguf/` | Ollama definition for supervisory setpoint generation |
| `scram-operator-lora/` | Operator adapter configuration, tokenizer and model card |
| `scram-observer-gguf/` | Ollama definition for v2 proposal judgments |
| `scram-observer-v3-gguf/` | v3 telemetry definition, adapter metadata and historical evaluations |
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
| `scram-observer` | `model/scram-observer-gguf/scram-observer-v2-q4_k_m.gguf` |
| `scram-observer-v3` | `model/scram-observer-v3-gguf/scram-observer-v3-q4_k_m.gguf` |

With Ollama installed and running:

```sh
ollama create scram-operator -f model/scram-operator-gguf/Modelfile
ollama create scram-observer -f model/scram-observer-gguf/Modelfile
ollama create scram-observer-v3 -f model/scram-observer-v3-gguf/Modelfile
ollama list
```

The manual studio additionally uses the stock `qwen2.5:1.5b` parser model
(`ollama pull qwen2.5:1.5b`). Manual buttons work without it.

The live operator falls back to deterministic baseline control when unavailable.
Observer routing tries the available models in the chains defined in
[`l2_monitor/observer.py`](../l2_monitor/observer.py). CPU corrections apply to v3
telemetry judgments; they are not a separate always-running observer.

## Training and evaluation

The operator pipeline is [`training/train_local.py`](../training/train_local.py),
using `training/train.jsonl` and `training/val.jsonl`. Its shared training/serving
format lives in [`l3_brain/prompt_format.py`](../l3_brain/prompt_format.py).
Training requires the separate [training dependencies](../requirements/train.txt),
a compatible GPU environment, and local llama.cpp conversion/quantization tools
under `.tools/llama.cpp` for export. The script documents its stages and paths;
training/export can replace local operator outputs.

The [observer notebook](../training/scram_observer_finetune.ipynb) now defaults to
`training/observer_clean/` and exports a separate candidate. The deployed v3
metadata and historical evaluations refer to the earlier datasets. This notebook
is not a promise of byte-identical reconstruction of historical v2/v3 weights.

See the [operator card](scram-operator-lora/README.md),
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
