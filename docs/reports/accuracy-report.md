# Accuracy by task

The newer released detector and parser measurements are documented in [ACCURACY_IMPROVEMENTS.md](accuracy-improvements.md). Figures below are historical and use different datasets.

These scores describe specific datasets and tasks. They are not guarantees for
all commands, future faults, operating points, or physical control outcomes.

| Component | Task | Measured score | Evidence |
| --- | --- | ---: | --- |
| scram-operator | Hold-versus-act agreement with expert | 100% | 235 historical validation cases: 33 hold, 202 act |
| scram-operator | All four target values exactly match expert | 53.62% | 126/235 historical validation cases |
| scram-observer v2 | Classify AI proposals | 97.78% | 927/948 historical unseen proposal examples |
| scram-observer v3 | Classify telemetry into normal/excursion/sensor/actuator | 62.50% | 30/48 fresh clean held-out windows |
| v3 + deployed CPU specialist | Same telemetry classification | 72.92% | 35/48 on the same fresh windows |
| CPU specialist alone | Telemetry classification | 77.71% | 366/471 clean held-out windows; not deployed alone |
| qwen2.5:1.5b alone | Exact command interpretation | 86.67% | 26/30 fresh authored commands |
| Parser with application checks | Exact command interpretation | 96.67% | 29/30 on the same authored commands |

The operator's average absolute target errors are 5.5 kPa pressure, 0.47
percentage points level, 3.51 kmol/h production, and 0 percentage points purge
composition. Valid JSON and correct hold-versus-act decisions do not mean its
continuous target values or resulting plant trajectories are always correct.

The v2 proposal observer historically detected all 569 suspicious proposals as
some attack class, while correctly naming 565 of them. It falsely flagged
17/379 normal proposals (4.49%). This is a recorded benchmark rather than a new
live evaluation of that model during this turn.

For fresh telemetry, excursion recall was 12/12 (100%) for both variants.
Sensor-fault recall was 6/12 (50%) for the raw LLM and 11/12 (91.67%) with the
specialist. Actuator-fault recall was 11/12 (91.67%) for both. However, **11/12
normal windows were falsely flagged (91.67% false alarms)**. Correctly recognizing
normal operation was only 1/12 (8.33%). Thus the telemetry subsystem still has a
serious discrimination problem despite strong abnormal-condition detection.

Older 480-window results were 71.46% raw classification accuracy and 74.17%
with specialist replay, with 7.5% false alarms. Those are different data and
historical inference outputs; they must not replace the less favorable fresh
results or be treated as the same estimate.

The standalone specialist's false alarms were 29.73% on its 471-window clean
holdout. It is used conservatively to correct confident missed faults, not as a
replacement for the telemetry LLM. Its independent validation accuracy was
62.35% on 409 telemetry windows, showing sensitivity to the recording split.

The parser's one failure was important: ?Open the coolant valve to 30%? became
a feed-1 command. Unsupported hardware is therefore not reliably refused.
This benchmark performed inference only; no simulator or valve was accessed.

## Deterministic systems

The last complete offline run passed **271/271 tests (100% test pass rate)**.
Its covered checks include 25 safety-interlock cases, 21 provenance cases,
11 Keel cases, 6 regulatory-controller cases, 9 governance cases and 7 sequential
monitor cases, plus application/race/format/data tests. Those percentages measure
software test success, not real-world accuracy or failure probabilities.

A historical trained-operator scenario rejected 47/47 forged commands (100%
within that scenario). Independent interlock and recovery scenarios also have
recorded successful trips. There is no sufficiently broad current closed-loop
benchmark to give SIS, L1, Keel, governance, rescue, or the complete project a
defensible operational accuracy percentage. No fresh Docker/Unity or hardware
validation was performed for this scorecard.

Machine-readable values and source paths are in
[artifacts/accuracy-scorecard.json](../../artifacts/accuracy-scorecard.json).
The fresh parser's complete corpus, raw outputs, corrections and failure are in
[artifacts/command-parser-accuracy.json](../../artifacts/command-parser-accuracy.json).
