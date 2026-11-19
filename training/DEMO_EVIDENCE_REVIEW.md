# Code-only demo evidence review

The demo launchers enable `SCRAM_OBSERVER_EVIDENCE_REVIEW=1` for the unified
measured-evidence-v2 observer. Other application launches retain their existing
default behavior. Model weights and model prompts are unchanged.

2026-11-19 demo tuning: evidence review v2 also recognizes a narrow explicit
pressure-and-production design-target claim when those numerical targets differ,
and repeated level readings dropping to zero and returning under stable pressure.
When an additional measured excursion is present it remains in the findings
list and is displayed separately. These heuristics were informed by development
cases and still need independent evaluation. Unconfirmed model/CPU disagreement
is not counted as normal. No model training or weight replacement was performed.

The model's label, score and reason remain available separately from the reviewed
decision. The control-room Protection layers panel displays the model label,
review outcome and numerical/source explanation. Unsupported alarms become
**Unconfirmed**, not normal. Incomplete histories and invalid readings cannot be
passed as healthy. Model scores are not treated as calibrated probabilities.

Proposal checks require a complete finite history. Drift support needs at least
three material moves, a net change beyond the existing sequential-monitor
allowance, and at least 80% agreement between the direction of those moves and
the net change. Constant histories and a single return-to-baseline correction
do not support repeated model drift claims. Explicit source tags and narrow
text-versus-target contradictions provide additional checks. A source tag is
not cryptographic authentication; the separate signing/provenance gate remains.

Telemetry checks use full aligned measured windows and controller commands.
Repeated abrupt zero-pressure readings with continuing inventory/feed flow are
instrumentation evidence. Rapid zero-to-operating-pressure recovery can support
dropout detection even when the controller changes flows in response. A single
zero remains unconfirmed. Sustained actual-command gaps are checked while
allowing a valve that is converging toward its command. Excursion checks use
existing soft monitor limits. A constant signal alone never proves a fault.

The existing hash-verified CPU physics detector supplies separate evidence.
Strong CPU findings remain visible even when the language model says normal;
weaker agreement only counts for sufficiently recent model windows. If the CPU
classifier fails, valid measured data remain available to the direct checks.
If measurements are invalid, the result stays insufficient/unconfirmed.

Only supported proposal findings contribute to the demo observer's governance
score. An unconfirmed review clears that observer evidence instead of keeping
an earlier alarm latched indefinitely. Independent sequential/rule-monitor
findings, governance recovery behavior, Keel, command signing and SIS remain
active. This is not a blanket disabling of alarms or protective action.

Run the integrated 3D control room with `RUN_SLM_CONTROL_ROOM.cmd` and refresh
the page after updating. `RUN_SLM_DEMO.cmd` now prints the raw model verdict and
an `evidence_review` result for supplied JSON. Direct Python inference can omit
`--evidence-review` when inspecting the model's raw behavior.

This is a demo decision layer, not improved model weights or a production release.
Unconfirmed includes genuine issues that these checks cannot establish. Limited
phrase checks cannot understand every operator intention. New thresholds and
newly composed decisions need a new independent evaluation before any accuracy
or production claim. The previously exposed model final test was not rerun or
used as a tuning benchmark for these changes.
