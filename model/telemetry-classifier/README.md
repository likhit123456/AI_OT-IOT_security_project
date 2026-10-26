# Earlier restricted telemetry specialist

The active runtime prefers the [physics detector](../telemetry-physics/README.md).
This classifier is the fallback when the physics policy is absent or disabled;
an integrity error does not silently select the fallback.

A CPU ExtraTrees classifier trained on measured-only telemetry and L1 command
windows from `training/observer_clean/`. Runtime inference uses a plain JSON
forest and Python's standard library, not pickle or scikit-learn. The model is
about 535 kB and requires no GPU memory.

The standalone classifier has a false-alarm tradeoff and is **not** deployed as
a replacement for the LLM. Only confident sensor/actuator findings may correct
an otherwise normal LLM telemetry verdict. Proposal judgments and governance
authority are unchanged. Original LLM labels and correction sources are logged.

`policy.json` enables this limited correction after 99 eligible validation findings
with no observed mistakes (assuming normal LLM verdicts). Its model digest prevents loading changed weights
under an old approval. This is a validation result, not a proof of safety.

The attempted sensor-noise suppression policy failed validation (three genuine
sensor faults would have been suppressed), so it remains disabled. Existing
LLM alarms are preserved by this restricted policy. The preferred physics
detector has a different correction policy, described in its README.

Reproduce the historical restricted-classifier experiment from the repository
root in a separate checkout (these commands overwrite candidate outputs):

```sh
pip install -r requirements-eval.txt
python -m training.build_clean_observer_dataset
python -m training.train_telemetry_classifier
python -m training.evaluate_telemetry_specialist
```

`training.benchmark_observer` now uses whichever specialist is approved (normally
the physics detector), so rerunning it does not reproduce the earlier restricted
classifier comparison. Use a separate `--output` path to preserve saved evidence.

`evaluation.json` records the standalone classifier, including its weaknesses.
`hybrid-evaluation.json` records development replay against historical LLM
outputs. `artifacts/fresh-observer-benchmark.json` records the saved paired local
Ollama inference. Neither the replay nor this small fresh sample establishes
deployment-wide accuracy. The original LLM weights were not retrained.
