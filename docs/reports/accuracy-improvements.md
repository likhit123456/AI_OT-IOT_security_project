# Accuracy improvements — 25 October 2026

The command parser now grounds actuator names in the original user request. A model response inventing a supported valve for a request about a coolant valve, heater, feed 3, or other unsupported hardware is rejected. Mixed commands retain supported actions and reject unsupported ones.

On the same 52 authored commands, actual Ollama inference improved from **40/52 (76.92%) to 52/52 (100%)**. This is a small authored test, not a universal language-understanding score. Evidence: `artifacts/accuracy-v2/parser-baseline.json`, `parser-corrected.json`, and the locked corpus/manifest.

## Measured telemetry detector

A small CPU forest uses measured valve/flow consistency, command/position differences, vapor/liquid mass and species balances, and pressure trends. Rounding tolerances and interval bounds prevent ordinary flow transients from looking like sensor failures. Overlapping short balances detect instrument failures that recover within a window. Inputs do not include scenario names, labels, rationale, or simulator fault truth.

Selection used training and validation recordings. An initial frozen candidate failed its independent test (86.55% accuracy) and was not deployed. That first test became development evidence. A second independently seeded collection, with ramping controls and newly simulated valve-capacity/sticking faults, remained unscored until the final model and feature implementation were frozen.

The final candidate passed the predeclared release criteria on **215 windows**:

A paired actual Ollama replay on these identical windows measured the previous v3 + v1 specialist pipeline at **138/215 (64.19%)**, versus **200/215 (93.02%)** for the new pipeline: **+28.84 percentage points**. Both had zero false alarms on these 31 normal cases. Sensor recall improved from 41.03% to 89.74%; actuator recall improved from 82.98% to 93.62%. Raw model outputs and both predictions are saved in `artifacts/accuracy-v2/paired-final.json`. This is a pipeline improvement; the LLM weights are unchanged.

| Task | Correct | Percentage |
|---|---:|---:|
| Overall exact four-class classification | 200/215 | **93.02%** |
| Normal classified correctly | 31/31 | 100% |
| Sensor fault recall | 105/117 | 89.74% |
| Actuator fault recall | 44/47 | 93.62% |
| Pressure excursion recall | 20/20 | 100% |
| False alarms among normal windows | 0/31 | 0% |

Remaining errors: eight sensor faults were called normal, four sensor faults were called actuator faults, and three actuator faults were called sensor faults. Zero observed false alarms across 31 normal windows does not establish a zero population false-alarm rate. Some fault variants share a base trajectory; windows are not all statistically independent.

Specific weaknesses remain: frozen level readings were missed in all six cases, two of four analyzer dropouts were missed, and four purge-flow failures were classified as actuator faults. Actual simulated capacity degradation was correctly localized in five of eight cases; all eight stuck-valve cases were correct. Aggregate scores should not hide these gaps. Further work needs additional independent recordings and another untouched test set.

These results use the isolated TE simulator, injected instrument corruption, synthetic command discrepancies, and actual simulated valve-capacity/sticking faults. They do not establish performance on industrial hardware, other process equations, or a broader operating envelope. The model is specific to the current TE constants, units, sampling and serving format. Validate again when those change.

## Runtime and scope

`model/telemetry-physics/policy.json` enables the released detector. Model, feature, validation and report hashes are checked before loading. Changed or invalid artifacts disable this path through the existing engine fallback. Malformed telemetry retains the parsed LLM verdict. The previous specialist remains available if the new policy is explicitly disabled.

The detector can correct both missed faults and false alarms in **advisory telemetry only**, preserving the original LLM label. It does not alter proposal assessment, governor authority, command execution, interlocks or the SIS. The observer LLM still runs; its weights have not been retrained. Operator target prediction and proposal-model accuracy are unchanged and must not be presented as improved.

## Reproduction and evidence

- `python -m training.train_physics_telemetry` selects on validation only. It overwrites the candidate; deployment hash checks then require new release evidence. Do not tune against the final holdout.
- `python -m training.evaluate_physics_holdout --holdout artifacts/accuracy-v2/telemetry-final-holdout.jsonl --freeze-name physics-final-frozen-candidate.json --output model/telemetry-physics/final-holdout-evaluation.json` checks the frozen candidate and dataset hashes.
- `python -m training.benchmark_physics_pair` runs non-actuating paired Ollama replay against the old specialist pipeline.
- Final holdout manifest: `artifacts/accuracy-v2/telemetry-final-holdout.manifest.json`. Test-only accelerated/fault-enabled C++ source and executable are under `artifacts/accuracy-v2/simulator`; production simulator source is separate.
- Full offline regression suite: **280 passed**, one existing Starlette multipart deprecation warning. Docker/Unity integration and hardware validation were not performed.

Historical figures in `docs/reports/accuracy-report.md` describe earlier pipelines and datasets. Different datasets should not be subtracted to claim a percentage-point gain; use the paired replay for that comparison.
