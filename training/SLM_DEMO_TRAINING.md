# Short demo adaptation, 2026-11-18

The user requested a usable demonstration with fewer epochs and less PC time.
This is a new experiment, `qwen05b-observer-20261118-demo01`, initialized from
the completed evidence-v2 checkpoint 135. The original completed experiment,
its optimizer states, older frozen candidate and specialist models are preserved.

`training.train_demo_observer` uses a fresh optimizer, learning rate 0.00002,
label-token weight 10, accumulation 4, and at most 24 updates. It makes one pass
over 96 cases from the existing training split, balanced across all eight
task/class combinations. Both examples and the budget are fixed before updates.
Validation uses 64 fixed development cases, eight per class, cycling through
independent validation recording groups. Input and recording overlap with the
training subset is rejected. No sealed measurements or final test are read.

Validation runs every eight updates. The run stops early when macro-F1 reaches
0.85, each class recall reaches 0.75, each class precision reaches 0.60, telemetry
normal false-positive rate is at most 0.25, and there are no invalid responses.
These are limited demo targets, not replacement release criteria. The best
checkpoint is retained by target pass, minimum class recall, macro-F1, then
fewer telemetry false alarms. The unchanged initialization is included as a
fallback. A checkpoint-boundary time limit of 40 minutes also prevents repeated
training if this machine slows down. No additional attempts are automatic.

Generations remain unconstrained JSON predictions from the language model.
The inference code takes supplied telemetry/proposal JSON, computes the same
measured evidence as training, and generates the verdict. It does not retrieve
validation answers or substitute fixed numeric predictions. This does not
establish generalization to unseen plants or independent operator language.

The completed run writes `completed.json`, `selected.json`, per-checkpoint
validation reports, updated adapter weights and optimizer states. Read those
actual results before claiming the target passed. The selected adapter may still
point to the initialization if all short updates perform worse.

For subsequent offline inference from the project root:

```powershell
.\.venv-train\Scripts\python.exe -m training.run_demo_observer --inputs readings.json
```

`readings.json` must contain the existing observer input object (with `input`
equal to `telemetry` or `proposals`), or a JSON list of such objects. The demo
runner preserves complete histories and refuses oversized inputs. Optional
`--output predictions.json` saves actual model outputs without overwriting an
existing result. It requires the pinned base, selected adapter, training
environment and CUDA; it does not start Docker or an inference server.

Application defaults remain V2/V3. A demo validation pass must never be presented
as a passed independent final test or sustained runtime release qualification.
