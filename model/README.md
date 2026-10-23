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

## Serving three models at once (operator + observer v2 + observer v3)

The live stack keeps `scram-operator`, `scram-observer` (v2: proposals, feeds
authority) and `scram-observer-v3` (telemetry, display only) resident in Ollama
together: ~1.1 GB VRAM each, peak ~4.8-5.2 GB on a 6 GB GPU, no reloads.

**Required on the serving machine:** set the user environment variable
`LLAMA_ARG_CACHE_RAM=0` and restart Ollama. Ollama 0.40.x's bundled llama-server
otherwise keeps a host-RAM prompt cache of up to 8192 MiB *per model runner*;
with three runners and ~2,450-token v3 telemetry prompts this exhausted a 16 GB
laptop's RAM in ~4.5 min. With the cap, each runner stays flat at ~1.9 GB
(experiments/observer_split_live_results.json, `system`). Every observer prompt
differs, so the cache bought nothing.

```powershell
[Environment]::SetEnvironmentVariable("LLAMA_ARG_CACHE_RAM", "0", "User")
# then quit Ollama from the tray and start it again
```
