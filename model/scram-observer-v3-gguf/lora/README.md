---
base_model: unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit
library_name: peft
pipeline_tag: text-generation
tags:
- lora
- sft
- unsloth
---

# SCRAM telemetry observer v3 LoRA

This adapter fine-tunes Qwen2.5-1.5B-Instruct to classify measured telemetry
windows as normal, excursion, sensor fault or actuator fault. The v3 input
includes commanded and actual valve positions. In the current control room,
v3 telemetry judgments are advisory; v2 handles proposal judgments that can
contribute to governance.

The [adapter configuration](adapter_config.json) records LoRA rank 16, alpha 16,
zero dropout and attention/MLP projection targets. Adapter weights and GGUF
weights are excluded from Git. Tokenizer/configuration files alone cannot run
this model. See [model setup](../../README.md) for registration instructions.

## Data and evaluation

Historical data is retained in `training/observer_v3_train.jsonl`,
`training/observer_v3_val.jsonl` and `training/observer_v3_unseen.jsonl` at the
repository root. The [saved unseen evaluation](../eval_observer_test_unseen.json)
contains 480 examples: 71.46% accuracy, 7.50% normal false-alarm rate and 21.67%
sensor-fault recall. These are historical sample results, not deployment-wide
accuracy. [Validation](../eval_observer_val.json) and
[training metrics](../train_metrics.json) are retained separately.

The [current notebook](../../../training/scram_observer_finetune.ipynb) defaults
to recording-separated data under `training/observer_clean/` and a separate
candidate export. It has not retroactively retrained these deployed weights.
Fresh held-out testing exposed substantial false alarms in the original LLM;
see [quality findings](../../../QUALITY_IMPROVEMENTS.md) and
[later accuracy work](../../../ACCURACY_IMPROVEMENTS.md).

## Runtime limitations

The approved [physics detector](../../telemetry-physics/README.md) can replace
the advisory telemetry label while preserving the original LLM label for
inspection. Its results must not be attributed to this adapter alone.
Neither telemetry model actuates protection or certifies plant safety.
The base model, simulation data and short telemetry windows limit applicability
outside this project. Refer to the repository [license](../../../LICENSE) and
the applicable base-model terms when distributing weights.
