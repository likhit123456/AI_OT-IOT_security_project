# First shared SLM: inspected validation failures

These observations concern the frozen 20261102 run04, selected adapter 135.
They do not change labels, thresholds, weights or final-test configuration.
Full inputs, targets, outputs and computed ranges are retained in the run's
`validation-difficult-cases.json`; original historical evidence remains intact.

| Input SHA-256 prefix | Expected / predicted | Inspection |
| --- | --- | --- |
| `cc08998e47097f97` | sensor_fault / normal | The answer claims pressure range 10.0 kPa; the actual 16-point range is 92.08 kPa. Feed flow and product flow also change, so the historical target's assertion of no related change needs human review. Neither explanation is authoritative. |
| `404bbba0b3cd1fae` | actuator_fault / normal | The measured/commanded purge position gap reaches 21.44 percentage points. The candidate's claim that valves follow their commands misses direct evidence. |
| `ead45fd15224637f` | sensor_fault / actuator_fault | Product flow contains two zero samples; every measured/commanded valve gap is zero. The answer invents a 14.4% product-valve gap. |
| `79f83e4ac4766017` | normal / actuator_fault | All measured/commanded gaps are zero. A fabricated 0.1% gap becomes an unsupported fault claim. |
| `e19c04e0c93b7c1c` | actuator_fault / sensor_fault | Product-valve gap reaches 16.33 percentage points; product-flow adjacent change is at most 0.13. The answer invents a jump down by 10.0 and selects the wrong fault family. |
| `6855b66dff093588` | excursion / normal | Pressure rises from 2729.71 to 2757.95 kPa. The candidate answers normal and reports range 10.0 rather than 28.24 kPa. Historical excursion thresholds are scenario-specific, but the numeric explanation is demonstrably wrong. |

The full Transformers validation produced no invalid outputs or cross-task
labels, yet missed many faults. Valid JSON and learned reason templates do not
establish correct numeric/temporal reasoning. Scores are uncalibrated. The
proposal validation uses constructed histories and few recording groups; its
66/66 result does not establish robustness to independent operator language.

Before another experiment, review historical label/explanation quality and
collect independent recordings, including challenging normal transitions and
gradual faults. Consider validated measured-only numeric features alongside the
complete histories, using the identical representation in training and serving.
That would require a new versioned experiment and untouched holdout, not changes
to this frozen candidate. A larger base is a possible later comparison; none was
substituted for the requested 0.5B experiment.
