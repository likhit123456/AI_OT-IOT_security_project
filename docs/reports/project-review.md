# SCRAM project review — 25 October 2026

> Historical review snapshot. A subsequent improvement pass fixed the safety,
> E-stop, observer reset/freshness, signing, port exposure, lifecycle and simulator
> source issues described below. See [QUALITY_IMPROVEMENTS.md](quality-improvements.md)
> for the follow-up results. Use the [README](../../README.md) and
> [architecture guide](../architecture.md) for the current layout and launch commands.

SCRAM is a substantial research prototype with a coherent central idea: an AI proposes operating targets, deterministic controllers turn those into actuator commands, and independent enforcement limits what reaches the simulated plant. The strongest work is the separation of responsibilities, the measured-versus-safety signal distinction, the attack scenarios, and the regression tests. It is more developed than the root README suggests.

It is not yet a production control system. The safety boundary is simulated in Python, the plant socket is unauthenticated, key material is embedded in the demo, and several recovery/concurrency cases need attention. These limitations matter when describing the project's guarantees. “Independent simulated safety channel” is accurate; this repository does not establish a physically independent hardware interlock.

## Scope and evidence

- Inventoried the 216 tracked files and mapped the active application, plant adapter, control, enforcement, monitoring, training, evaluation, packaging, and legacy/reference areas.
- Reviewed the active execution paths and their tests; inspected the C++ plant source, dataset builders, model-serving code, representative evaluation artifacts, and launch/deployment configuration.
- Parsed all 74 tracked first-party Python source files outside the upstream/reference directories: no syntax errors. Inventory and exact dataset overlap counts are in [artifacts/review_inventory.json](../../artifacts/review_inventory.json).
- This is a source review and local test run, not an exhaustive proof of every file. Model weights, compiled Unity assets, third-party toolchains, virtual environments, packet captures, and every individual training example were not manually audited. Models were not retrained or benchmarked during this review.
- Browser checks use intercepted API fixtures. They do not call Docker, Ollama, or the real plant. Screenshots explicitly substitute a test placeholder for the live 3D reactor. A complete live scenario rehearsal remains outstanding.

## How the project fits together

| Area | Responsibility | Assessment |
| --- | --- | --- |
| `shared/contracts.py` | State, supervisory intent, actuator commands | Good seam. Composition units and the meaning of `truth` need more precise documentation. |
| `l0_plant/` | TCP adapter, safety latch, command provenance, fault injection | Clear separation. Secondary-hazard handling and transport assumptions need strengthening. |
| `l1_enforcer/regulator.py` | Four PI loops, bumpless transfer, command signing | Considerably stronger than the old stub; toy-plant tests cover useful behavior. |
| `l3_brain/` | Baseline and local LLM operators, prompt formatting, output validation | Shared training/serving format and hold-on-failure behavior are good choices. |
| `l2_monitor/`, `l1_enforcer/` | Rule and sequential monitoring, observer inference, governance, Keel | Deterministic enforcement is a strong foundation. Observer lifecycle and evidence freshness need work. |
| `live/` | Full-stack engine, scenarios, operator console | Main product/demo experience. Single-owner plant I/O is useful, but the engine is large and its action queue is unbounded. |
| `sim_app/` | Separate direct-control sandbox and AI rescue | Useful comparison environment; it intentionally lacks the full SCRAM protections. Emergency-stop precedence needs repair. |
| `training/`, `model/` | Collection, expert labels, fine-tuning, exported models, evaluations | Reproducible prompt formatting is a strength. Observer validation contamination is confirmed below. |
| `experiments/` | Adversarial comparisons, frontier sweeps, live rehearsals | Valuable evidence, but recorded results are historical runs rather than validation of this change. |
| `simulation/`, `packaging/`, `docker/` | Upstream source, image/service integration, visualization | Distinguish local source from the prebuilt image actually executed. |
| `reference/`, `legacy/l1_stub.py`, `legacy/run_demo.py` | Older implementation and minimal demo | Keep clearly labeled and remove them from the primary onboarding path. |

The active control path is `live.Engine → AIOperator → L1Regulator → Keel → L1 signer → PlantService`. The gate checks provenance; the safety layer uses the independent simulated sensor path. Observer telemetry is advisory/display-oriented, while proposal evidence can affect governance. The manual station is a different path and can write directly to the simulator.

## Frontend changes delivered

Both interfaces now share graphite surfaces, mint accents, quieter borders, clearer typography, and responsive layouts. The full control room has a navigation rail on desktop and a compact navigation bar on small screens, distinct overview/console/analysis headings, readable instruments, a defense pipeline, and redesigned valve cards. The manual station places pressure and controls ahead of the large plant viewport and clearly labels its direct-control mode.

Existing endpoints and control flows are retained. The control-room CSS and JavaScript have been extracted from its monolithic HTML into separate assets. Tab semantics, keyboard arrow/Home/End navigation, a skip link, an accessible command label, and reduced-motion support were added or improved.

Run `node tests/frontend_smoke.cjs` to generate current previews under `artifacts/frontend/` using test telemetry.

## Bugs fixed in this change

| Issue | Previous behavior | Change |
| --- | --- | --- |
| Non-finite operator authority | `min(1.0, NaN)` could turn a malformed authority value into full authority. | Reject non-finite values before changing the cap. |
| Non-finite manual valve input | A NaN/Infinity `value` or `delta` could reach clamping and produce an unintended extreme valve setting. | Reject these at the request-model boundary. Internal direct calls still deserve their own validation. |
| Stale engine connection status | `Engine.read()` returned the cached healthy snapshot during reconnect, even after `self.status` changed. | Always overlay current engine status/error on the telemetry snapshot. |
| Premature SIS status | Initial HTML claimed “SIS ARMED” before receiving any state. | Initial/disconnected status is “SIS UNKNOWN.” |
| Stalled control-room polling | A fetch that never completed stopped subsequent polls. | Five-second request timeout and HTTP-status validation. |
| Persistent connection warning | A recovered connection could retain its old failure banner. | Clear connection warnings on recovery. |
| Restarted server lost history/events | Client cursors belonged to the old server sequence and hid new low-numbered events. | Per-engine run identity resets local cursors and cached chart/log state. |
| Incomplete tab accessibility | Tabs lacked selected state, panel relationships, and expected arrow-key navigation. | Added ARIA relationships, roving focus, and keyboard navigation. |
| Default pytest collection failed | Pytest imported the root Modbus utility and failed on an optional legacy dependency. | Limit default collection to `tests/` using `pytest.ini`. |
| A passing test leaked a real connection | The concurrent-reset test's background thread attempted to reconnect to a real simulator, producing an unhandled exception warning. | Mock its post-reset read; verify with thread warnings treated as errors. |

## Outstanding bugs and risks, in priority order

### High: a low-level trip ignores subsequent overpressure or invalid SIS readings

In [l0_plant/safety.py](../../l0_plant/safety.py), `evaluate()` only selects a cause before the initial latch. `safe_state_inputs()` immediately returns `{e_stop: 0, product_valve_sp: 0}` for `level_low`, before checking later pressure or validity. A trip at 4% level followed by 3100 kPa still closes only product and explicitly clears E-stop; a later invalid safety reading behaves the same way. This is reproducible without a simulator.

Preserve the original trip reason for diagnosis, but evaluate active hazards continuously and apply an explicitly designed priority/escalation policy. Add combined-hazard tests and validate the resulting lineup against the live model. This change deliberately does not invent a new safety-control policy.

### High: emergency stop is blocked during AI rescue

[sim_app/server.py](../../sim_app/server.py) checks `rescue.active` before permitting manual E-stop, so the request returns HTTP 409. [sim_app/static/app.js](../../sim_app/static/app.js) also disables the E-stop button while AI control is active. Furthermore, the rescue loop clears simulator E-stop when encountered.

The emergency-stop action should cancel rescue and take priority over pending AI writes, with one coordinated ownership/locking path. Merely enabling the button is insufficient: test an E-stop while inference is in flight, ensure no later write clears it, and require deliberate release.

### High deployment risk: the unauthenticated plant port is published on all host interfaces

[docker-compose.yml](../../docker-compose.yml) publishes `55555:55555` and `80:80`. The plant protocol has no authentication and accepts direct writes. The gate can detect/reassert after an out-of-band write, but cannot prevent it. Actual remote reachability depends on host/network firewall rules; external exploitation was not tested.

For a local-only demo, bind to loopback. For a multi-host lab, use explicit network isolation and writer allowlisting. The fixed demo HMAC keys in `live/engine.py` are also not deployment secrets. These are deliberate demo assumptions that should not be presented as hardened isolation.

### Medium: an observer result can survive a reset

`Observer.reset()` clears history and verdicts, but a pending `_run()` can finish afterward and write the previous run's verdict back into `last`. A controlled delayed response reproduced post-reset suspicion of 0.9. Use a generation identifier captured at dispatch and discard results from older generations. The existing rescue run-ID pattern provides a useful model.

Related freshness concern: `Governor` expires observer evidence, but `Observer.suspicion()` has no age check and can continue returning an old proposal alarm to other consumers. Apply a shared freshness contract at the observer boundary.

### Medium: observer validation contains training prompts

The checked-in observer v3 data contains **six exact shared user turns** between train and validation: 3,429 unique train turns and 1,195 unique validation turns. The operator dataset has 2,190 unique train turns and 235 unique validation turns, with zero exact overlap.

`training/build_observer_dataset.py` shuffles a pool of recorded and synthesized windows into train/validation without globally deduplicating that boundary. Its unseen-set filtering only checks against training. Deduplicate before splitting, retain rollout/window provenance, and split by rollout or scenario family with temporal gaps. Rebuild data and rerun evaluation before relying on the existing validation score. The amount by which scores are inflated was not measured.

### Medium: bundled C++ source contains an out-of-bounds steady-state read

`simulation/simulation/TE_process.cc` assigns the purge valve from `b[3]`, outside its three-element solution vector. This is already documented in the repo and partially mitigated by `scripts/reset_plant.py` writing the correct purge setting after restart. The source defect remains.

Fix and test the actual simulator build, then pin/distribute that image. Editing this local source alone does not update the prebuilt image used by Compose.

### Medium: bundled C++ TCP parser does not implement stream framing safely

In `simulation/simulation/main.cc`, the server reads up to 1,024 bytes into `buffer` and passes it to `reader.parse(buffer, command)` without terminating at `valread` or accumulating complete newline-delimited messages. TCP reads can split or combine messages; the code also does not handle a negative `read()` result separately. Partial `send()` results are ignored. These are source-level defects; the running prebuilt binary was not inspected to establish whether it has identical code.

Use a bounded per-client receive buffer, parse complete frames by explicit length, handle read/send errors and partial writes, and test fragmented and coalesced requests.

### Low: command signing authenticates rounded rather than exact actuator values

`provenance_gate._tag()` serializes valve values to four decimal places. Changing a signed valve value from 20 to 20.00001 leaves the tag valid and is admitted. The physical change is tiny, but an assertion that every modification is rejected is too strong. Either quantize the command before both signing and applying it, or sign an exact canonical representation.

### Documentation and lifecycle gaps

- The README still leads with `legacy/run_demo.py` and the L1 stub. The current product is `python -m live` with `L1Regulator`, Keel, governance, and observers.
- Composition fields in `PlantState` are documented as mol %, while actual readings are fractions; both regulators now perform the conversion. The README's unresolved conversion item is stale.
- Architecture text says reset is always deliberate, while the live engine automatically rearms after a clean window with a repeated-trip lockout. Document demo recovery separately from the base interlock contract.
- `sim_app` starts a perpetual polling thread but does not stop/join it in lifespan shutdown. Repeated app lifecycles in one process can accumulate workers.
- `live.__main__` can persist a user environment variable and terminate/restart Ollama-related processes on normal launch. Make that an explicit setup operation; `--no-ollama-fix` currently opts out.
- `requirements/runtime.txt` does not provide an installable test dependency set: pytest is missing and httpx is commented out. There is no automated test workflow in `.github/workflows`.

The first, second, fourth, and signing findings can be reproduced safely with `python -m scripts.review_findings`. That script starts no plant/model services and performs no simulator writes.

## Improvements worth prioritizing

1. Resolve emergency-stop precedence, secondary-hazard escalation, and stale observer generations. Add race/combined-hazard tests before changing safety behavior.
2. Repair dataset separation and rerun held-out evaluation across unseen rollouts, fault severities, and scenario families. Compare the trained operator against both baseline and labeling expert; accurate imitation alone does not demonstrate a better controller.
3. Establish a reproducible local environment: development dependencies, a pinned simulator image digest, model/version manifests, and CI for the offline suite plus browser smoke tests.
4. Make the active entry point obvious. Consolidate current architecture/recovery documentation and mark historical experiments and stubs explicitly.
5. Split `live/engine.py` into orchestration, scenario handling, snapshots, and recovery. Add bounded action queues and observable completion/failure results instead of treating “queued” as completed.
6. Track a telemetry timestamp independently of HTTP responsiveness. Retain last-known values for diagnosis but visibly mark them stale, and prevent ordinary commands when the engine cannot execute them reliably.
7. Consolidate duplicated design constants and UI styles after the visual direction is accepted. Self-host fonts if offline operation is a requirement. Add chart descriptions and keyboard-accessible numeric summaries for a fuller accessibility pass.

## Validation and running the result

The final offline Python suite passed **249 tests** with background-thread warnings treated as errors. JavaScript syntax checks and `git diff --check` passed. The browser suite covers both applications at desktop and narrow widths, all three control-room tabs, keyboard navigation, chat dispatch, reconnect behavior, server restart recovery, viewport visibility, and horizontal overflow. No browser JavaScript errors were observed in those cases. Initial sandbox runs could not access pytest's temporary folder; the final run used the approved access and completed successfully.

The two live interlock integration tests were excluded: they reset and manipulate a running GRFICS plant. The Docker simulation, Unity rendering, model inference, and physical recovery traces were not exercised end-to-end for this change. One dependency deprecation warning remains from Starlette's multipart import.

For the full control room, run `python -m live` and open `http://127.0.0.1:8765`. Its existing defaults may start/reset the simulator and reconfigure Ollama. To attach to an already-running plant without those startup operations, use `python -m live --no-reset --no-ollama-fix --no-browser`. The manual studio is `python -m sim_app` at `http://127.0.0.1:8770`. Avoid operating both writers against the same plant simultaneously.

Run offline tests with `python -m pytest -q --ignore=tests/test_interlock_truth.py`. Run browser checks with `node tests/frontend_smoke.cjs` after installing Playwright; the script also accepts `PLAYWRIGHT_MODULE` and `PLAYWRIGHT_CHROMIUM` paths for an existing installation.
