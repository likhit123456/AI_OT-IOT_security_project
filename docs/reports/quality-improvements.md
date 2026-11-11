# Quality improvement pass — 25 October 2026

This pass improves reliability, dataset integrity, evaluation coverage, and
telemetry fault detection. It does **not** establish that every AI metric has
improved. In particular, fresh evaluation exposes an unresolved false-alarm
problem in the telemetry LLM. No original LLM weights or historical evaluations
were overwritten, and no model alarms were suppressed to manufacture a score.

## Cleanup

Removed **6,202,172,602 bytes (6.20 GB)** across 16 generated directories:
two intermediate full-precision observer exports and first-party Python,
pytest, Ruff, and Unsloth caches. The deployable GGUF models, LoRA adapters,
checkpoints, training environment, datasets, source, reference material and
evaluation evidence were retained. Exact paths and sizes are in
[artifacts/cleanup-manifest.json](../../artifacts/cleanup-manifest.json).

The exports can be regenerated from the retained adapters and their base model.
Ollama uses the GGUF models, not the deleted full-precision exports. The training
environment is retained because it is useful for reproducible training.

## Reliability and control behavior

- E-stop cancels AI rescue and serializes with valve writes. A model response
  arriving after cancellation cannot write valves. Rescue no longer releases
  E-stop; release is deliberate. The UI keeps E-stop available during AI control.
  A failed stop write remains pending and is reasserted when the connection returns.
- The safety latch retains the first trip cause while responding to secondary
  hazards. Combined overpressure/low inventory shuts both feeds, opens purge,
  and closes product; this avoids the simulator's ordinary E-stop opening the
  product valve into a low vessel. Safety-sensor loss escalates to latched
  E-stop. These are explicitly tested simulation policies requiring further
  physical validation before deployment claims.
- Observer reset invalidates pending inference generations. Old responses
  cannot repopulate evidence. Proposal suspicion expires after 30 seconds of
  control-time age.
- HMAC signing uses exact canonical float representations. Changes below the
  former four-decimal rounding precision are now rejected.
- Plant ports bind to localhost. Runtime signing/logging keys are generated per
  process unless `SCRAM_L1_KEY`/`SCRAM_KEEL_KEY` are supplied; keys are not printed.
  The TCP plant protocol itself remains unauthenticated, and same-host/same-process
  compromise remains outside this boundary.
- The action queue is bounded at 64 entries and processes at most eight queued
  actions before another control cycle. HTTP request bodies are bounded at 64 KiB.
- Telemetry age is exposed independently of HTTP responsiveness; stale telemetry
  is marked and ordinary frontend controls pause rather than acting on old data.
- Manual-station polling has an idempotent start and a shutdown/join path.
- Ordinary startup no longer writes user environment settings or restarts
  Ollama. Those operations require the explicit `--configure-ollama` option.

## Simulator source

The local C++ source now uses the valid purge-solution element `b[2]`, frees
steady-state solver buffers, and fixes the delayed-composition loop's swapped
condition/increment. TCP handling accumulates bounded newline-delimited frames,
handles fragmented/coalesced requests, rejects invalid input ranges and trailing
garbage, retries partial writes, and closes excess/oversized connections.

The source compiled in Ubuntu WSL with `-Wall -Wextra -Werror`. An isolated
process on a temporary port passed framing, malformed-request, input-bound,
initial-purge, E-stop and frame-size checks. The app's plant was not reset.

**Compose still uses the prebuilt GRFICS image.** Source fixes do not silently
replace its binary or prove equivalence to its extended fault-injection behavior.
The Python purge workaround remains necessary for that image. Docker Desktop
was stopped during this pass; full image/Unity/plant rehearsals remain untested.

## Clean observer data

`python -m training.build_clean_observer_dataset` creates versioned inputs under
`training/observer_clean/`. Entire source recordings are separated before fault
augmentation, so synthesized children remain with their parent recording.

- Zero shared user prompts across train, validation and unseen splits.
- Zero shared selected source recordings across those splits.
- Exact duplicates are removed, and **477 ambiguous/conflicting user inputs**
  are excluded rather than teaching different labels for identical telemetry.
- Pool shortages are reported; validation/test counts are not padded with
  duplicate examples. The clean splits contain 3,273 / 1,094 / 471 total
  examples, including proposal examples. Telemetry-only counts are smaller.
- The manifest records source recordings, class counts, seed, shortages and
  dataset hashes. Historical v3 files are retained for their existing weights.

The observer notebook now defaults to clean inputs and a separate candidate
output directory. Future LLM retraining will therefore not overwrite the
deployed models by default. This pass trained the CPU specialist below; it did
not retrain the three existing LLMs.

## New advisory telemetry specialist

A **535 kB CPU ExtraTrees classifier** was trained on 1,796 clean telemetry
windows. It consumes only measured signals and commanded valve positions.
The exported JSON forest needs only Python's standard library at runtime;
NumPy/scikit-learn are training/evaluation dependencies. It adds zero GPU memory.

Its standalone false-alarm tradeoff failed the desired replacement criteria.
It is therefore used only for confident sensor/actuator findings when the LLM
otherwise says normal. On independent validation recordings, that restricted
policy qualified **99 candidate corrections with zero observed label errors or
false alarms**, assuming a normal LLM verdict. Actual correction frequency depends
on the LLM's answers; that validation did not rerun the LLM. The approved model
digest is checked at load time.

Corrections retain the original LLM label and identify their source in logs/UI.
Telemetry judgments remain advisory and do not affect governance authority.
The proposal path stays on the existing observer v2.

### Historical benchmark replay: 480 windows

This replays saved LLM predictions, so it is development evidence rather than
a fresh blind evaluation of new LLM weights.

| Metric | Historical LLM | LLM + restricted specialist |
| --- | ---: | ---: |
| Accuracy | 71.46% | **74.17%** |
| Sensor-fault recall | 21.67% | **32.50%** |
| Attack/fault detection | 64.72% | **69.17%** |
| Normal false-alarm rate | 7.50% | **7.50%** |
| Excursion recall | 99.17% | 99.17% |
| Actuator-fault recall | 72.50% | 72.50% |

Some corrections detect an abnormal window but name the wrong fault family;
those remain classification errors. Full confusion matrices and corrections
are in [hybrid-evaluation.json](../../model/telemetry-classifier/hybrid-evaluation.json).
The standalone candidate's weaker results are retained in
[evaluation.json](../../model/telemetry-classifier/evaluation.json).

### Fresh local Ollama comparison: 48 balanced clean held-out windows

The same raw inference response is used for the baseline and corrected result.
Model IDs, dataset/input hashes, raw responses and timings are stored in
[artifacts/fresh-observer-benchmark.json](../../artifacts/fresh-observer-benchmark.json).
There are only twelve examples per class, so these estimates are uncertain.

| Metric | Fresh LLM | With specialist |
| --- | ---: | ---: |
| Valid structured output | 100% | 100% |
| Accuracy | 62.50% | **72.92%** |
| Sensor-fault recall | 50.00% | **91.67%** |
| Attack/fault detection | 83.33% | **97.22%** |
| Normal false-alarm rate | **91.67%** | **91.67%** |
| Excursion recall | 100% | 100% |
| Actuator-fault recall | 91.67% | 91.67% |

The high false-alarm rate is a real weakness exposed by the stricter held-out
recordings. These numbers must not be mixed with the older 480-example sample
to imply deployment accuracy. The specialist improves missed-fault detection
without increasing this sample's false alarms, but does not solve them.

An attempted minimum-effect-size filter would have dismissed three genuine
sensor faults in validation. It was rejected and is not part of runtime alarm
handling. More representative benign data, repeated/severity-stratified trials,
and a separately validated LLM candidate are needed before lowering alarms.
Operator target errors and other unchanged historical metrics are not claimed
as improvements.

## Verification and reproducibility

- **271 offline Python tests passed**, with background-thread warnings treated as
  errors. One dependency deprecation warning remains in Starlette's multipart import.
  Regression tests cover combined hazards, E-stop during inference,
  pending-write cancellation, observer reset races, evidence expiry, queue
  floods, exact signatures, lifecycle shutdown and actual dataset separation.
- Browser smoke checks cover desktop/tablet/mobile layouts, command dispatch,
  keyboard navigation, reconnect/restart, stale telemetry, and E-stop access
  during AI control. No browser JavaScript errors were observed.
- Linux simulator compilation and isolated protocol checks passed.
- Added pinned development/evaluation dependencies and GitHub Actions jobs for
  offline tests, browser checks and simulator compilation/protocol checks.
  The workflow is configured locally; it has not been run by GitHub in this pass.
- `README.md` now leads with the current control room, documents units and
  recovery semantics, and labels historical stubs explicitly.

Use the commands in [README.md](../../README.md) to reproduce. JavaScript syntax checks,
targeted Ruff checks and `git diff --check` also passed. The broader
`live/engine.py` decomposition and a fully pinned replacement GRFICS image are
still worthwhile follow-up engineering work.
