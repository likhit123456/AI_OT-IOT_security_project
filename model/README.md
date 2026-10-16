# model/

All trained operator-model outputs go here (fine-tune checkpoints, adapters,
merged weights, eval reports). Large weight / checkpoint files are gitignored
(see the repo `.gitignore`); configs, tokenizer files, metrics and eval reports
are small and stay in git.

Training data: `training/train.jsonl` / `training/val.jsonl`, built by
`training/build_dataset.py` from real GRFICS states. The prompt format the
model is trained on is defined once in `l3_brain/prompt_format.py` and is the
same one `l3_brain/policy.LLMOperator` serves with; a model trained on an older
format must be retrained.
