# Project evaluation — 2026-11-18

## Assessment

SCRAM has a credible architecture and substantial working implementation for a
local simulation demonstration. It includes a real trained observer, deterministic
control, layered constraints, simulated safety-channel protection, provenance
checks, and a usable dashboard. It is not yet consistently reliable for an
unrehearsed demonstration: several interaction, startup, and presentation defects
remain. Its current evidence does not qualify it for real industrial control.

This review evaluated the current working tree, rather than only committed Git
files. It covered the control-room and manual-studio entry points, HTTP actions,
command parsing, control lifecycle, safety and provenance code, observer routing,
frontend state and 3D bootstrapping, dependency declarations, CI, and saved model
evaluation evidence. It was not an exhaustive line-by-line proof of every archived
experiment, third-party asset, notebook, or simulator implementation.

**Historical evaluation:** the findings below describe the 2026-11-18 review.
The code/UI remediation and fresh verification are recorded in
[CURRENT_STATUS.md](CURRENT_STATUS.md). The independent combined-detector
evaluation remains open. This original evaluation added reports and isolated
reproduction scripts; it did not change application behavior or retrain models.

## Findings, in practical priority order

### 1. High: negated chat commands can execute the opposite instruction

Location: `live/commands.py:44`, `live/commands.py:99`, `live/engine.py:913`.

Confirmed by calling the actual parser without any plant connection:

| User text | Actual parsed action |
| --- | --- |
| `do not open feed 1` | Manual feed 1 opening: **100%** |
| `do not release the valves` | Release manual control |
| `do not raise pressure by 50` | Increase pressure target by 50 kPa |

The router recognizes action words without accounting for negation. The live
chat handler consumes these parsed actions directly. Downstream Keel/SIS limits
still exist, but they cannot restore the user's intended meaning.

Fix: reject ambiguous/negated instructions before dispatch, preserve clear
affirmative commands, and add negative-language regression cases. A generic
language-model fallback is not sufficient protection for this deterministic path.

### 2. High: single-writer protection is incomplete across entry points

Locations: `live/__main__.py:120`, `sim_app/__main__.py:26`,
`training/launch_demo_control_room.py:180`.

The demo launcher checks the standard app ports and holds a launch mutex. Ordinary
`python -m live` only reserves its own HTTP port, while `python -m sim_app` has no
shared plant-owner check. Starting the manual studio beside the control room, or
starting another control room on a different web port, can therefore give two
applications write access to the same simulator. The README warns against this,
but code does not consistently enforce it. Startup reset can also disturb the
first application.

This is a source-confirmed gap; no competing writer was launched during the
evaluation. Fix: acquire one shared owner lease keyed to the simulator endpoint
before any reset or write, across both application entry points. Keep intentionally
injected out-of-band writes explicit as attack scenarios.

### 3. Medium: reset can race with HTTP action validation

Locations: `live/server.py:105`, `live/engine.py:277`.

The HTTP handler validates `expected_run_id` against a snapshot, then discards it.
`Engine.submit` captures whatever run ID is current later. A reset between those
steps can turn an old-run request into a new-run action. The existing protection
correctly cancels requests already queued before reset, but misses this interval.

An isolated deterministic interleaving reproduced HTTP 200 and execution in
`new-run` for a request carrying `expected_run_id: old-run`. The simulated action
only appended to a Python list; it sent no plant command.

Fix: pass the client's expected run through to dispatch and validate it against
the same identity retained by the queued action, including immediate actions.

### 4. Medium: action endpoint does not enforce the browser-origin boundary

Location: `live/server.py:94`.

The handler accepts JSON bytes with `Content-Type: text/plain`, ignores `Origin`
and Fetch Metadata, and does not require a request token. The isolated handler
fixture accepted a request with an unrelated HTTPS origin and `cross-site`
metadata, returning 200 and calling its fake action dispatcher. Loopback binding
does not itself constitute an application-level origin check.

This confirms server behavior, not an end-to-end browser exploit: browser local
network permissions and other browser restrictions were not tested. Fix: enforce
the intended local UI origin/host policy and JSON content type, and consider a
session-bound action token. Account explicitly for the supported Vite development
proxy. Exercise rejection with fixtures, not with the live plant.

### 5. Medium: clean setup and CI rely on undeclared `psutil`

Locations: `training/launch_demo_control_room.py:14`,
`training/local_gguf_server.py:12`, `tests/test_demo_deployment.py:6`,
`tests/test_app_lifecycle_review.py:11`, `.github/workflows/quality.yml:14`.

No project requirements file declares `psutil`, but the demo launcher and modules
imported by tests require it at import time. Local tests pass because this PC
already has it. An isolated import probe that makes that package unavailable
fails immediately with `ModuleNotFoundError`.

This is a reproducibility/CI dependency gap; a newly provisioned CI job was not
run. Fix: declare the appropriate runtime/demo dependency and ensure the test
install includes it. Verify installation and test collection in a clean environment.
The CUDA runner also has a machine-specific Ollama executable path, so the full
demo is not a turnkey cross-platform package despite the portable weight export.

### 6. Medium: 3D loading feedback can disappear before Unity is ready

Locations: `live/static/reactor.html:39`,
`frontend/src/components/panels/PlantView.tsx:28`.

The reactor bootstrap removes the loading overlay after 20 seconds regardless
of successful Unity startup. The parent iframe load event establishes document
load, not Unity readiness. A slow or failed large asset load can consequently
leave a blank/stalled viewport with no useful progress message.

Executing the actual inline bootstrap in a Node VM with a never-ready Unity
fixture confirmed the overlay was removed at 21 seconds while `calledRun` remained
false. This is a bootstrap logic test, not a real WebGL rendering result.

Fix: keep progress visible until an actual ready signal, display explicit load
failure/timeout state, and provide retry. Include slow and failed asset cases in
browser tests; the existing browser smoke fixture substitutes the reactor view.

### 7. Low: some status labels and architecture documentation overstate state

Locations: `frontend/src/components/panels/ProtectionLayers.tsx:58`,
`ARCHITECTURE.md:68`, `ARCHITECTURE.md:85`.

- The UI says “AI observers on” when model aliases are configured, even without
  checking current worker/backend health. Its alternate “rules + CPU detector”
  wording also assumes the optional detector is loaded. Report configuration and
  availability separately using existing runtime status fields.
- The architecture document says `truth` is only for evaluation, despite its
  intended use by the simulated safety channel and Keel.
- It says a secondary hazard cannot change the latched safe state. Current
  safety code correctly retains the first cause while escalating protection for
  additional hazards. Documentation should describe that distinction.
- Some historical reports still describe earlier unavailable live startup. They
  are valid dated records, but README links should direct readers to a current
  status summary so old blockers are not mistaken for the latest state.

## What is working well

- **Layer separation:** operator outputs are supervisory targets, with finite
  value checks and bounds. Regulation, command checks, and simulated safety
  protection remain deterministic. Measured observer inputs are separate from
  the independent simulated safety readings.
- **Protection behavior:** hard trips, hysteresis, missing safety-sensor handling,
  combined-hazard escalation, command HMAC checks, replay rejection, and readback
  auditing have dedicated tests. Direct simulator writes remain detectable and
  bounded rather than cryptographically prevented at the simulator socket.
- **Observer execution:** bounded shared work, separate role histories, request
  identity, deadlines, generation cancellation, and fallback paths keep inference
  separate from the control loop. CPU detection has its own measured window.
- **Artifact integrity:** the preferred PhysicsDetector loaded successfully under
  its model/feature/evaluation hash checks. The demo F16 deployment and saved
  validation identity also verified successfully.
- **Honest model presentation:** raw predictions remain available alongside
  evidence-reviewed findings. Unsupported alarms are “unconfirmed,” which is
  more accurate than counting them as confirmed normal cases.
- **Recent reliability repairs:** default tests now skip simulator mutation;
  startup recovery, basic duplicate-launch handling, stale-telemetry detection,
  malformed API errors, and process cleanup have regression coverage.
- **Current runtime sample:** the existing app reported running, no error,
  telemetry age 0.17 s, CPU status `ok`, 1,176 observer calls and zero recorded
  inference failures. This supports operational availability at sampling time,
  not detection correctness or proof that every newly saved fix is loaded.

The safety path is independent within the simulated design. It runs in the same
application/simulation environment; it is not physically separate safety hardware
or a guarantee against arbitrary host compromise.

## Model quality: interpret the results by class and system version

The selected demo uses real fine-tuned weights. Its saved independent primary
model-only evaluation reports macro-F1 **94.33%**, but that number hides relevant
telemetry weaknesses:

| Saved demo model-only result | Observed count |
| --- | --- |
| Constructed proposal cases correct | 409/409 |
| Actuator faults correct | 40/40 |
| Excursions correct | 43/43 |
| Sensor faults detected | 32/40; **8 missed** |
| Normal telemetry correct | 30/40; **10 false alarms** |
| Constructed pressure-dropout stress detected | 4/20; **16 missed** |
| Frozen-level stress support | 0 qualifying cases |

These are saved historical test results, not newly rerun evaluations. Windows are
correlated within a small set of recordings, and proposal text is constructed.
They do not establish equivalent performance on arbitrary operator language or
new plants. The preferred CPU detector has a separate historical 215-case result
of 93.02% accuracy; that is a different model and test population.

The recent code-only evidence review changes the combined decision system.
Its development live demonstrations are useful, but the entire combination has
not received a fresh independent accuracy evaluation. Its accuracy cannot be
inherited from the raw model's 94.33% macro-F1 or the CPU detector's accuracy.
The current runtime sample contained unconfirmed raw drift/sensor alarms; without
ground-truth annotation of that interval, they cannot be counted as measured
false positives in this review.

For this demo, prioritize the interaction and 3D fixes before spending more time
training. Then run a short fixed demonstration script with normal operation,
sensor dropout, actuator fault, suspicious proposal, recovery, and a slow 3D load.
Measure confirmed alarms, misses, false alarms, unconfirmed outcomes and latency
separately. Keep previously exposed final recordings out of any new unseen test.

## Verification and evidence

- Fresh Python suite: **448 passed, 2 skipped, 1 dependency warning**, 17.90 s.
  The skipped cases mutate the live simulator; they were not enabled.
- Fresh frontend regressions: **9 passed**. Fresh TypeScript build check passed.
- The previous source-review production Vite build passed; it was not rebuilt
  again for this read-only application evaluation.
- Five isolated problem probes confirmed parser negation, reset dispatch race,
  foreign-origin handler acceptance, missing dependency failure, and premature
  3D loading-overlay removal.
- No Playwright package/browser was available here. Full browser/WebGL behavior,
  clean-machine installation, new CI execution, and an independent combined
  detector evaluation remain unverified.
- No live action requests, plant resets, fault injections, additional model
  generations, training, or model-file modifications occurred in this evaluation.
  The existing app/model processes were preserved.

Evidence and reproduction files:

- [Python fixture probes](artifacts/evaluate_project_20261118.py)
- [Probe results](artifacts/project-evaluation-probes-20261118.json)
- [3D bootstrap probe](artifacts/evaluate_3d_loading_20261118.cjs)
- [3D result](artifacts/project-evaluation-3d-20261118.json)
- [Fresh test report](artifacts/project-evaluation-tests-20261118.xml)
- [Read-only runtime sample](artifacts/project-evaluation-runtime-20261118.json)
- [Earlier functional repairs](artifacts/SOURCE_REVIEW_20261118.md)
- [Historical demo model evidence](artifacts/optimization/candidates/qwen05b-observer-20261118-demo01/NEXT_STEPS_REPORT.md)

Recommended order: command meaning and shared writer ownership; reset dispatch
and action-origin checks; declared dependencies; truthful 3D/status feedback;
then a rehearsed end-to-end demo and independent combined-decision evaluation.
