---
base_model: unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit
library_name: peft
pipeline_tag: text-generation
tags:
- lora
- sft
- unsloth
---

# SCRAM operator LoRA

This adapter fine-tunes Qwen2.5-1.5B-Instruct for supervisory control of the
GRFICS chemical-plant simulation. It consumes measured plant state and emits
JSON supervisory targets for pressure, level, production and composition, or a
hold decision. It does not directly write valves in the SCRAM control room.

The [adapter configuration](adapter_config.json) records LoRA rank 16, alpha 16,
zero dropout and attention/MLP projection targets. Tokenizer metadata is
included; adapter weights and the exported GGUF are excluded from Git.
See [model setup](../README.md) for required files and Ollama registration.

## Training and evaluation

[`training/train_local.py`](../../training/train_local.py) trains on
[`training/train.jsonl`](../../training/train.jsonl), with
[`training/val.jsonl`](../../training/val.jsonl) for validation. The targets are
expert-policy labels for simulated GRFICS states. The training/serving format is
shared through [`l3_brain/prompt_format.py`](../../l3_brain/prompt_format.py).

The saved [training log](../train_log.json) records 2,190 training examples,
235 validation examples, one epoch, learning rate 0.0002, batch size 1,
gradient accumulation 8 and maximum sequence length 1,024.
The saved [validation report](../eval_val.json) records 235/235 valid parsed
outputs and 33/33 hold decisions recalled. These historical results measure
agreement with the validation labels, not physical safety or generalization
to arbitrary plants. [Ollama evaluation](../eval_ollama.json) is recorded separately.

## Intended use and limitations

Use for simulation research and demonstrations with the project's deterministic
control and protection layers. The separate manual studio uses this model in
its own rescue loop and does not provide SCRAM's full protection path.
The adapter inherits dependence on its base model, prompt format and dataset.
Model output alone is not a safety controller. The repository's
[license](../../LICENSE) covers its code; base-model and tooling terms must also
be respected when obtaining or redistributing model weights.
