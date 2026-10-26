# Advisory physics-feature telemetry detector

This is the currently preferred CPU correction model loaded by
`approved_specialist()` in [`monitor/telemetry_classifier.py`](../../monitor/telemetry_classifier.py).
It uses measured telemetry and command/response features from
[`monitor/physics_features.py`](../../monitor/physics_features.py), with a JSON
forest evaluated using Python's standard library. It needs no GPU.

For v3 telemetry, it supplies the advisory classification and preserves the
original verdict as `llm_label`, with source `llm+physics-detector`. Unlike the
older restricted classifier, it can revise an existing alarm as well as a
normal verdict. It does not contribute to proposal authority or protective
actuation and is invoked through the LLM telemetry path.

## Release evidence

[`policy.json`](policy.json) enables the model only with matching SHA-256 hashes
for the model, feature source, validation and final evaluation report. The
[final report](final-holdout-evaluation.json) also checks the frozen candidate
and records the passed gate. Git preserves the evidence bytes across checkouts.

The final 215-example holdout records 93.02% accuracy, 0 observed normal false
alarms, 89.74% sensor-fault recall and 93.62% actuator-fault recall. The sample
includes simulated trajectories and explicitly synthetic faults. These results
do not establish physical deployment safety or a zero population false-alarm
rate. See [accuracy improvements](../../ACCURACY_IMPROVEMENTS.md) for the
candidate history and remaining limitations.

## Reproduction and changes

Training is implemented in
[`training/train_physics_telemetry.py`](../../training/train_physics_telemetry.py);
the freeze/holdout protocol is in
[`training/evaluate_physics_holdout.py`](../../training/evaluate_physics_holdout.py).
Those scripts write model/evaluation outputs, and their default paths do not
automatically reconstruct every final-release artifact. Inspect their arguments
and use a separate checkout for experiments. Preserve the frozen evidence.

To verify the bundled release without retraining, run from the repository root:

```sh
python -m pip install -r requirements-dev.txt -r requirements-eval.txt
python -m pytest -q tests/test_physics_detector.py
```

If the physics policy is absent or disabled, the loader can use the
[older restricted classifier](../telemetry-classifier/README.md). An integrity
failure raises an error; the live engine reports the unavailable specialist
instead of silently approving changed files.
