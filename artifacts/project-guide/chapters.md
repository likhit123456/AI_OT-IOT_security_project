# SCRAM: the complete project learning guide

Prepared from the local workspace on 1 November 2026. This is a reading and code-navigation guide, not a new experimental run. Numerical evaluation results are identified as saved evidence. The accompanying inventory records file sizes, source line counts and SHA-256 hashes for the exact snapshot.

## 1. How to use this book

Read chapters 2 through 10 first to rebuild your mental model. Then follow the file catalogue in chapter 19 while opening the source companion. The catalogue covers every Git-tracked file, in path order. Python entries include a symbol map with source locations; the separate Source Companion contains numbered source and a mechanically extracted explanation of each function's control structure. Source-derived notes are navigation aids, not a substitute for the conceptual explanations in this book.

The first file to understand is contracts.py, but the first file executed for the main application is live/__main__.py. There is no single 'last file': this is a set of persistent services, browser programs, training tools and experimental entry points. The primary runtime ends only when its loop is stopped. Files under experiments/ and training/ are separate programs, not successive steps in ordinary application startup.

Scope: all 298 tracked files are catalogued. All tracked, readable code and configuration are included in the source companion, including historical implementations. Large datasets and tokenizer/model tables are described by their structure rather than printing millions of values. Binary images, PCAPs and video are catalogued as assets. The generated Unity loader is included in the TXT source companion, but its minified implementation is omitted from the PDF to keep it usable. Local dependencies, Git objects, model weights and generated runtime files are counted separately in workspace-summary.json; they are not treated as project-authored code. The notebook's code cells are included separately.

## 2. What you built

SCRAM is an AI-supervised chemical-process simulation. A simulated reactor produces measurements; an AI or deterministic policy proposes operating targets; conventional control turns those targets into valve positions. Several independent checks constrain or reject commands before they reach the process. The application demonstrates why plausible AI output, anomaly detection, command authentication and physical safety are different responsibilities.

The four manipulated valves are feed 1, feed 2, purge and product. Opening feed valves adds reactants. Opening purge releases vapor and helps lower pressure. Opening product drains liquid inventory. These relationships explain the signs in the control equations and the cause-specific safety actions. Closing every valve is not a universal safe action: a pressure excursion needs a relief path, while low inventory needs the product outlet closed.

The main control room is live/ on port 8765. The reactor studio is sim_app/ on port 8770. Both can address the same simulator on port 55555, but the studio directly controls the simulator without SCRAM's Keel, provenance gate or L0 interlock. Studying them as interchangeable frontends would be a serious misunderstanding of this codebase.

The project includes three local language-model roles: scram-operator proposes setpoints, scram-observer evaluates proposal history, and scram-observer-v3 evaluates telemetry windows. A small CPU forest adds a fourth, non-language-model role: it corrects advisory telemetry classifications using process features. Model training and evaluation are separate from runtime control.

@figure architecture.png | Main runtime and trust boundaries. The browser requests actions; the engine owns control. The telemetry classification branch does not feed authority.

## 3. Repository geography and execution paths

contracts.py defines shared data. l3_brain/ owns policy and authority blending. monitor/ contains rule monitoring, model observers, governance and CPU telemetry inference. l1_regulator.py implements the current PI controller. enforcer/keel.py constrains commands using the safety channel. l0_plant/ connects to the simulator, verifies command origin and enforces trips.

live/ composes these pieces into a continuously running application. sim_app/ is the separate manual/rescue station. training/ produces datasets and models. model/ contains serving configuration, adapters' metadata, CPU forests and evaluation reports. experiments/ holds scenario runners and saved results. scripts/ contains reset, validation and collection utilities. tests/ captures intended behavior and regression cases.

simulation/ is inherited simulator source, remote-I/O bridges and visualization assets. packaging/ provides a Debian/systemd distribution path. reference/sim_cpp_unused/ is a shelved reactor variant. artifacts/ preserves evaluations, frozen candidate evidence and cleanup manifests. images/ contains inherited GRFICS screenshots; these are not screenshots of the current SCRAM control room.

There are four useful reading journeys: runtime (live -> L3 -> L1 -> Keel -> L0); a sensor reading (simulator -> PlantState -> observers -> UI); a model (recordings -> labels -> chat examples -> fine-tuning -> GGUF -> Ollama); and a regression (test case -> fake dependency -> assertion -> implementation).

## 4. Contracts, units and the two sensor views

PlantState is a frozen dataclass containing measured pressure, level, flows, compositions, actuator feedback, cost, interlock status, optional truth and a timestamp. Frozen dataclasses discourage accidental in-place mutation. as_dict() makes them convenient for JSON and logs. SupervisoryTargets contains pressure_sp, level_sp, production_rate_sp, a_comp_sp, authority and rationale. A None setpoint means hold that loop; it does not mean a zero target.

ControlCommand contains four absolute valve percentages plus seq and auth_tag. is_finite() rejects NaN and infinities. clamped() returns a copy restricted to 0..100, preserving the provenance fields. The check must precede clamping because ordinary min/max expressions can give misleading results with NaN. The signer must sign the final values after any downstream edits.

Units: pressure is kPa; inventory level and valve position are percent; flows are kmol/hour; measured A/B/C composition is a fraction from 0 to 1. The composition target is mol percent: 0.47 measured means 47.0 for a_comp_sp. L1's _a_pct() and the authority blend make this conversion explicitly. The ordinary Python control path uses engineering units; the legacy Modbus bridges perform 16-bit register scaling separately.

The deployed simulator interface supplies outputs (the independent simulated safety-channel view), measured (the faultable instrumentation view), and state (actuator feedback). PlantService._to_state() publishes measured fields and retains outputs in PlantState.truth. The name 'truth' is historical: the current implementation also deliberately uses it for L0 safety and Keel. The AI prompt and measured-only detectors must not consume it.

@figure signals.png | One physical process, two different observation paths. A fault in ordinary instrumentation can mislead the controller while the safety channel still observes a limit crossing.

The safety channel is independent by simulation assumption, not physically independent hardware. The detector may use truth to assign evaluation labels while still keeping truth out of model features. That distinction is how you evaluate a detector without leaking the answer into its input.

## 5. Startup and one complete control cycle

python -m live resolves live/__main__.py. main() parses port, brain selection, production limits, reset behavior, browser behavior and explicit Ollama configuration. plant_up() probes port 55555. If the simulator is unreachable, startup invokes docker compose up -d simulation and waits. An unavailable operator model causes a baseline fallback. Ordinary startup does not persistently reconfigure Ollama; --configure-ollama opts into that operation.

Engine.start() launches a control worker. _connect() optionally calls reset_and_settle(), creates a fresh provenance gate, starts PlantService with SafeHold, clears fault injections, constructs L1, Keel, rule monitors and Governor, loads an approved telemetry specialist, and selects the brain. Attaching with --no-reset skips a simulator restart but still clears sensor/actuator faults during connection; it is not a read-only attachment.

The engine uses a bounded action queue of 64 entries. HTTP threads validate and enqueue actions rather than sharing a plant socket freely. The control worker executes them. Shared snapshots are protected with a lock; stop uses a threading.Event. LLM calls run on background threads so a slow response does not block normal PI regulation. The loop has a nominal 0.05-second polling cadence, not a hard real-time scheduling guarantee.

During _cycle(), the brain receives the latest measured PlantState and emits authority-blended targets. BehaviorMonitor checks measurements and targets. SequentialMonitor scores the original pre-blend intent, preventing the authority blend itself from looking like a new staircase attack. The split observer receives both proposal history and measured telemetry paired with the preceding command.

Governor updates its applied authority ceiling from corroborated proposal-side evidence and sequential rules. L1 regulates the already-produced targets using actual elapsed time. Manual overrides replace selected L1 valve requests. Keel evaluates those requests against SisFrame. L1 then signs Keel's final command. PlantService audits readback, admits or rejects provenance, applies a normal or protective write, and returns the next PlantState.

The ordering matters: targets are computed before the current governor update, so updated authority generally affects subsequent targets. Telemetry is paired with the command that produced the sample, not the command about to be issued. After actuation, the engine records trips, gate events, trends, model decisions and recovery state for browser polling. Deliberate attack scenarios can inject a separate forged write or replace the final signed command to demonstrate failure boundaries.

@figure cycle.png | The normal cycle. A trip overrides upstream requests; signing occurs after Keel changes the valves.

Engine._run() distinguishes action errors from connection failures, reconnects as needed and maintains status. read() returns incremental histories and events using sequence cursors. run_id lets the browser detect a new engine session; stale measurements must not be displayed as fresh connected data. History is bounded to 3,600 samples at approximately two samples per second, about 30 minutes.

## 6. L3: policies, prompts, validation and authority

Policy is a small interface: decide(state) returns SupervisoryTargets. BaselineOperator is a deterministic fallback. LLMOperator supplies scheduling, cached results, parsing and failure behavior; OllamaOperator supplies the local HTTP request to /api/chat. make_policy() and the engine choose between implementations. Runtime model choice is different from whether a training checkpoint exists on disk: Ollama must have the requested model registered.

prompt_format.py is shared by training and serving. measured_dict() selects allowed fields and rounding, state_to_json() serializes the user turn, and system_prompt() supplies role, design targets and response contract. Sharing this code prevents a fine-tuned model from receiving a different input format at inference. A standing operator note is appended through the policy interface, and decision logs retain which note produced a response.

parse_llm_targets() extracts a JSON object, reads only recognized setpoint fields, rejects booleans and non-finite numeric values, treats missing/null setpoints as hold, and clamps finite values to Envelope. Pressure targets are 2400..2900 kPa, level 30..70%, production 60..180 kmol/h, and A composition 40..55%. Rationale is bounded. Extra keys, including invented valve commands, do not become actuations.

An inference failure becomes hold targets. This differs from startup fallback: if Ollama is unavailable at selection, the engine may select the baseline policy; a malformed response from an already-selected model is handled by that policy's hold behavior. The current Ollama operator normally refreshes about every five seconds while the fast control cycle reuses its cached decision.

AIOperator.step() saves last_intent before applying authority. Effective authority is min(operator slider, Keel budget, Governor applied ceiling). At A=1 the request is unchanged. At A=0 all setpoints become None. Between those extremes, each numeric target becomes current*(1-A) + requested*A. Example: measured pressure 2700, requested 2800 and A=0.25 yields a 2725 kPa target. Composition uses percent on both sides of the blend. Non-finite authority fails closed to zero.

Authority is permission to deviate, not a probability that the model is correct. Holding a valve is also not an emergency stop: an existing unsafe lineup can remain unsafe. Independent downstream protection is necessary even if the AI emits only holds.

## 7. L1: understand the controller mathematically

PI implements u = kp*error + integral. Each call conditionally integrates ki*error*dt and clamps u between its output limits. If the controller is saturated and the error would push farther into saturation, it stops accumulating integral. This is anti-windup: without it, a valve could remain pinned long after the measured process comes back toward target.

Pressure uses error measured-pressure minus target-pressure. Positive error opens purge. Its controller output range is -6..100. Negative output means purge is already closed, so a split-range trim raises the feeds up to 50% above their base requests to rebuild pressure. The purge valve itself never receives a negative position.

Level also uses measured minus target. A high measured level opens product and drains inventory. Production uses feedforward DESIGN_F1*production_target/100 plus a slow PI trim on desired minus measured product flow. While pressure is borrowing feed authority through split range, the production integrator is held to avoid fighting pressure recovery. Composition uses target A-percent minus measured A-percent to move feed 2.

The four gain pairs (kp, ki) are pressure (0.30, 0.003), level (4.0, 0.04), production (0.02, 0.002), and composition (1.5, 0.02). These gains belong to this simulator and its units. dt is clamped to 0..1 second to bound integral updates after a pause. Output values are clamped again in ControlCommand.

Bumpless transfer means matching controller memory to the currently applied valve position. PI.track(u,e) sets integral=u-kp*e. L1Regulator.bumpless() seeds all loops on connection/re-arm. A loop with a None target holds its associated actuator and marks itself held; _resume() seeds it when the target returns. This avoids a sudden command jump caused by old integral memory.

Worked example: pressure=2750 and target=2700 gives error=50. With integral=0.3926, the proportional-plus-integral request is approximately 15.3926% purge before the next integral update. This is an illustrative calculation of PI, not a recorded plant response. Keel and L0 can still change what is actually applied.

l1_stub.py remains for older experiments. It is not the current live controller, and saved results using the stub should not be attributed automatically to the new L1 regulator. The studio's sim_app.ai.regulate() is another separate controller with its own rescue policy.

## 8. Keel, provenance and the L0 safety interlock

Keel receives a proposed ControlCommand, measured state, trusted SisFrame, and explicit time. It maintains rolling safety-channel histories. It checks proximity to trip limits, least-squares pressure/level rates, projected time to trip, cumulative pressure rise or level loss, and persistent disagreement between measured and safety readings. A divergence finding costs authority but has severity zero; it is not itself used to choose a physical safe lineup.

Default soft pressure margin is 150 kPa inside the hard trip. Rate limits are 2 kPa/s pressure rise and 0.01 percentage-points/s level loss. The pressure rise budget is 150 kPa in 120 seconds; the level drop budget is 3 percentage points in 300 seconds. Rate estimates need multiple samples and sufficient span. These checks catch trajectories that can evade single-step limits.

Keel.step() updates authority, chooses the strongest hazard severity, blends selected valves toward a cause-specific lineup, limits ordinary valve slew, and returns Decision. Safety-directed moves are exempt from ordinary slew limits. Its own authority loses budget on physics evidence or provenance rejections and recovers after a clean period. Low authority sustained long enough sets divert. The live integration passes zero L2 suspicion to Keel; L2 operates through Governor instead. Older comments describing direct L2-to-Keel routing are historical.

EventLog appends canonical JSON bodies with the previous MAC and signs each body with HMAC-SHA256. Verification can detect modification and broken links within a supplied retained sequence. Because the deque is bounded and verification accepts its first previous-MAC anchor, this is not proof that an attacker did not truncate an entire prefix/suffix. Tamper-evident logging is not a durable externally anchored audit service.

CommandSigner increments seq and signs the four float values using exact float.hex representations. ProvenanceGate.admit() rejects non-finite, unsigned-in-strict-mode, stale/replayed or incorrectly tagged commands. Admitted commands become clamped last_good. A rejected command falls back to last_good; without a seeded fallback the service holds rather than applying an invalid request. Production live wiring uses the gate, although PlantService can be constructed with gate=None for legacy callers.

Readback audit detects a different issue: writes sent directly to the unauthenticated simulator bypass admit(). It compares actuator feedback and E-stop with expected state, allowing 0.5% valve tolerance. It records mismatches, and the normal following write reasserts control. This is polling-based detection and correction, not prevention or a strict timing guarantee during disconnects. A stolen signing key can authenticate an attacker command; the stolen-key scenario deliberately demonstrates that the safety interlock must still protect the process.

PlantService.read() translates a state and evaluates the safety channel. apply() audits provenance, chooses admitted/fallback commands, checks the latched safety state, performs a normal/protective write, then translates the response and evaluates safety again. A newly discovered post-write trip is therefore latched for the next protective actuation; the Python loop is not asynchronous hardware protection.

TripLimits defaults: pressure >=3000 kPa, level <=5% or >=95%. Reset requires finite pressure <=2800 and level within 15..85%. The live demo instead raises the low trip to 40% and low re-arm to 42% to make slow inventory attacks observable in a reasonable demonstration. --production-limits restores base thresholds.

SafetyInterlock preserves the first trip cause for diagnosis but continues evaluating secondary hazards. Missing or non-finite safety readings latch sensor-fault protection. A low-level trip closes product and holds other valve requests. Pressure/high-level trips use classic E-stop unless configured with SafeHold. The live engine supplies SafeHold: feeds go to 60% of design, purge regulates toward 2650 kPa and product toward 50% level. Pressure at or above 3050 escalates to latched E-stop.

Combined high pressure and low inventory shuts feeds, opens purge, and closes product. That combination matters because this simulator's E-stop opens both purge and product; blindly using E-stop on low inventory would drain more liquid. Sensor-fault protection takes precedence over this combined-hazard mode.

@figure safety.png | Protective action selection. First-cause diagnosis is retained, but secondary hazards can change protection.

The base reset API is explicit and checks the safety channel again. The live demo additionally attempts automatic re-arm after ten continuous seconds in the valid band, with at most three automatic re-arms in 600 seconds before lockout. Re-arm restores design valves, seeds controllers, clears stale monitoring evidence and initially caps AI authority at 0.5. Clearing faults, clearing a standing model note, resetting the interlock and restarting the simulator are four distinct actions.

## 9. Monitoring and governance

BehaviorMonitor observes measured bounds, implausibly fast changes, suspiciously stale signals and setpoint steps. A settling grace period suppresses stale-signal checks during expected connection/recovery behavior, while other checks remain. A frozen value is not automatically proof of an attack: a stable process may legitimately have nearly constant level.

SequentialMonitor examines the trajectory of requested setpoints. It keeps positive/negative cumulative sums per channel, same-direction run length, a small lexical screen of rationale text, and corroborating Keel event codes. Evidence decays with elapsed time. Reversal reduces opposite-direction evidence; reissuing an identical target does not count as another move. Its score is suspicion, not a protective command.

The split observer uses v2 for proposal checks and v3 for telemetry. Proposal fallback is rule-monitor-only, not v3, because the saved proposal replay found v3 unsuitable for this role. Telemetry can fall back from v3 to v2 and then no model verdict. Worker generation counters discard late results after reset. A shared in-flight gate limits concurrent observer inference. Stale evidence expires instead of holding authority indefinitely.

Proposal-side labels provenance_violation, rationale_mismatch and setpoint_drift can contribute suspicion. A model's 'provenance_violation' label is a judgment about proposal content; it is not the cryptographic verification performed by ProvenanceGate. Telemetry labels normal, excursion, sensor_fault and actuator_fault are advisory in this integration.

Governor requires repeated evidence: two consecutive qualifying samples from a monitor, with rule calls sampled roughly once per second. It maps corroborated suspicion to a piecewise ceiling and rate-limits applied authority downward at 0.25 per second and upward at 0.03 per second. Recovery additionally needs 15 clean seconds and intent near the design envelope. Observer evidence becomes stale after 30 seconds. Accumulated residual rule evidence can decay faster once the proposal returns to the envelope; new evidence still counts.

@figure governance.png | Actual static ceiling() mapping from monitor/governance.py. Applied authority follows this target with separate drop/recovery rates and recovery conditions.

At suspicion below 0.30 the target ceiling is 1.0; it falls toward 0.55 by 0.60 and toward 0.25 just below 0.90. At 0.90 or greater the floor is 0.15, or 0.10 for provenance_violation. Note the deliberate step at the final threshold. These are maximum permissions, and the human cap or Keel can impose a lower value.

## 10. CPU telemetry detector and release evidence

telemetry_classifier.py implements dependency-free forest inference. It validates the window, constructs features, checks feature count, walks every exported tree by comparing a feature with a threshold, averages leaf probabilities, and selects the highest-probability class. Its score is 1 minus the normal-class probability. A forest confidence is not a guarantee that its class is correct.

The old specialist only upgrades an otherwise normal LLM verdict to a sufficiently confident sensor/actuator finding. It preserves existing alarms. PhysicsDetector overrides that correction policy: its result can replace either a normal or abnormal parsed LLM verdict, while retaining the original as llm_label. The specialist is invoked only after a valid telemetry LLM response; it is not a standalone always-on safety detector when Ollama is absent.

physics_features.py compares measured valve positions with commanded positions, and measured flow with expected valve-flow relationships. Feed expectations are cv*position; purge/product expectations additionally use sqrt(max(pressure-100,0)). Rounding tolerance is subtracted before residuals are normalized, so ordinary two-decimal measurement formatting does not create fake faults.

It also estimates vapor inventory as pressure*(122-0.3*level)/(8.314*373), liquid inventory as 2.49*level, and checks aggregate/species balances over full, half and overlapping short windows. Rates convert seconds to hours to match kmol/hour. Flux intervals use adjacent-sample minima/maxima to allow legitimate steps between samples. Pressure slope and soft excursion indicators add hazard information. Scenario names, known fault labels and safety truth are not features.

approved_specialist() first tries approved_physics_detector(). policy.json must enable it, final-holdout-evaluation.json must report a passed gate, and SHA-256 digests for model, feature implementation, validation and final report must match. An enabled-but-modified release raises an error; it does not silently pretend to be approved. The engine reports the specialist unavailable. Explicitly absent/disabled physics policy allows the older specialist path to be considered.

The frozen final holdout contains 215 windows: 31 normal, 117 sensor faults, 47 actuator faults and 20 excursions. Saved evidence reports 200 correct (93.02%); sensor recall 105/117, actuator recall 44/47, and 31/31 normal classified correctly. A saved paired replay on identical windows compares the previous pipeline's 138/215 with the new pipeline's 200/215. Those are pipeline results; the LLM weights were not improved by this classifier change.

@figure evaluation.png | Saved final-holdout class recall, with sample counts. Data comes from model/telemetry-physics/final-holdout-evaluation.json; this documentation task did not rerun inference.

Remaining errors are meaningful: six frozen-level cases were missed, two analyzer-dropout cases were missed, and four purge-flow faults were called actuator faults. Zero false alarms in 31 normal windows does not establish a population false-alarm rate of zero. Some cases share base trajectories and some faults are synthetic. This evaluation does not validate industrial deployment.

## 11. HTTP, browser code and operator actions

live/server.py uses ThreadingHTTPServer from the standard library and binds to loopback. GET /api/state?h=...&e=... returns a snapshot and incremental histories/events. POST /api/action validates JSON, allowed action name, body size and arguments, then submits work to the engine. A successful submission is acceptance into the queue, not necessarily completion of a plant action. Static paths are resolved under live/static; GRFICS/Unity assets are proxied from the plant web service on port 80.

live/commands.py is the control-room chat router. Recognized valve commands become manual overrides, release phrases drop overrides, setpoint phrases become canonical instructions, and free text becomes a model note. A manual override still passes Keel, signing and L0. The operator LLM itself never chooses the direct override valve values.

live/static/index.html defines the page structure and element IDs. control-room.js owns application state, poll cursors, event filters, tabs, chat, action handlers, gauges and Canvas trends. renderInstruments() updates pressure/tank indicators; renderRecovery() explains interlock state; renderStack() shows layers; renderBrain()/renderObservers() show model evidence. chart()/spark()/draw() turn accumulated series into plots. A lost connection should affect status rather than quietly keep an old green snapshot.

telemetry-workspace.js adds signal selection, table/chart modes and a read-only exploration view of the same stream. CSS files separate base control-room layout, shared visual styling and workspace-specific presentation; inspect stylesheet order when tracing which rule wins. reactor.html embeds the Unity scene independently of the inherited dashboard. The renderer is a visualization consumer, not the controller of record.

sim_app/server.py uses FastAPI/Pydantic and a shared Plant object. It serializes socket operations and write-sensitive transitions, tracks current values/history, handles reset/reconnect, and exposes state, manipulation, natural-language command, rescue and stop endpoints. A rupture flag latches at the app's 3100 kPa threshold and resets with the plant. It mirrors a UI convention; the C++ simulator clamps pressure and does not implement an actual explosion state.

sim_app/static/app.js polls around every 0.5 seconds, creates valve controls/gauges, renders activity cards and charts, queues natural-language commands, and fits the embedded 3D view. JSDoc annotations and tsconfig.json support static checking without a frontend build step. index.html supplies the controls; style.css, nocturne.css and workspace.css provide the presentation.

The studio's parse_commands() calls stock qwen2.5:1.5b with a constrained action schema, bounds action count to eight, and then grounds each action in the user's real words. 'to 40' means absolute set; 'by 5' means relative adjustment. Direction words settle signs; unsupported/invented actuators and negated valve clauses are rejected. Mixed supported and unsupported clauses can report partial success. Unknown commands return 422, unavailable parsing 503, malformed input 400, and refused control actions 409 as appropriate.

Rescue is a separate approximately two-second model/control loop. It uses scram-operator targets and the studio's deterministic regulate() mapping. It finishes after three consecutive near-design decisions and restores the design lineup. Stop and E-stop are different: Stop AI relinquishes rescue; E-stop cancels rescue, blocks late AI writes and requires deliberate release. Generation/run identity and serialized writes protect against stop/restart races. Failed E-stop transmission remains pending and is reasserted on reconnect.

## 12. Simulator, protocols and deployment variants

docker-compose.yml uses the prebuilt fortiphyd/grfics-simulation image with pull_policy: missing. It does not build simulation/Dockerfile. Ports 80 and 55555 publish on 127.0.0.1. A bridge subnet gives the container 192.168.95.45 and NET_ADMIN permits its inherited interface setup. A read-only supervisord override starts the simulator and web services without the Modbus writers that would otherwise compete with SCRAM.

GrficsClient owns a TCP socket and a binary file wrapper. _txn() writes one JSON object plus newline, flushes, reads a complete response line and decodes JSON. read() sends request=read; write_inputs() sends request=write with data.inputs. Timeouts and disconnect errors propagate so the engine can reconnect. A transport client does not decide safety.

The bundled C++ main.cc supports up to 30 client slots using select(). A pending string buffer per client handles fragmented TCP frames and multiple newline-delimited frames in one read. Inputs are checked for finite valve percentages in 0..100 and boolean/0-or-1 E-stop. Oversized pending frames close a connection. send_frame() handles partial writes and interrupted sends. SCRAM_SIM_PORT supports isolated simulator checks on a temporary port.

TE_process.h declares process state, parameters and methods. TE::TE() initializes constants and calls steady_state(). steady_state() solves a three-variable linear system with LAPACKE to determine steady flows and valve positions. The corrected purge expression uses b[2]. update() applies E-stop or provided setpoints, computes elapsed wall-clock hours, calculates volume/pressure/flow/composition, advances species inventories using Euler integration, clamps outputs, and updates an economic cost. print_outputs() is diagnostic; get_state_json() serializes outputs/state.

Physics: total vapor moles are A+B+C; pressure follows NV*R*T/vapor_volume. Liquid D determines inventory level. Reaction consumes A and C and produces D. Feed 1 contains A, B and C, while feed 2 contributes A; inert B must leave through purge. The reaction law is kpar*(partial pressure A)^1.2*(partial pressure C)^ncpar. Flow coefficients and gas/volume constants are mirrored by the telemetry features, which is why changing the simulator can invalidate the detector.

Important source/runtime distinction: the bundled simulation/simulation/TE_process.cc serializes outputs and state but does not implement the deployed faultable measured channel. The prebuilt image, the local basic source, the historical reference variant, and the accelerated fault-enabled evaluation source under artifacts/accuracy-v2/simulator are different artifacts. Compiling the local basic source does not automatically provide the full interface expected by live/. Its isolated protocol tests do not establish full runtime equivalence.

The inherited Modbus device files bridge the JSON process to holding/input registers. feed1/feed2/purge/product handlers write valve setpoints from holding registers and publish scaled position/flow input registers; tank and analyzer publish corresponding measurements. modbusdevice.py owns async server setup and simulator transport helpers. remote_io.py/server_async.py preserve older pymodbus.server.async imports, which are invalid Python 3 syntax now that async is reserved. They are historical compatibility code, not current live entry points.

simulation/web_visualization includes UnityLoader.js, build metadata, templates, PHP data/version endpoints and an MP4. Generated loader code boots a prebuilt Unity application; original Unity scene/C# sources are not present in this tracked tree. data/index.php bridges HTTP to the simulator's JSON socket. The browser explosion effect is separate from physical state integration. The local frontend and the prebuilt image may ship different asset sets.

packaging/build-simulation-deb.sh builds a Debian package; DEBIAN/control supplies package metadata, postinst/prerm manage install/removal lifecycle, systemd units start simulator/Modbus/interface setup, nginx serves visualization, and vm-overlay.js adds VM-oriented presentation. This is an alternate deployment route, not part of python -m live startup.

@figure plant.png | Existing images/sim.png: inherited GRFICS 3D plant view. Included to orient you to the simulated process, not as a newly captured SCRAM screenshot.

## 13. Data collection, labels, fine-tuning and export

collect_states.py runs named simulator rollouts with a controlled target schedule or expert policy, samples measured state at 2 Hz, and retains truth_eval_only for scoring. Completed rollouts are appended atomically from temporary files; reruns can skip completed recordings. collect_observer_states.py adds fault recordings with the command actually sent beside each measured response, which is essential for actuator-fault examples.

expert_policy.py labels operator examples. It loads design targets from training/design_point.json and uses production TripLimits to back off near hazards, hold on unusable measurements/trips, or maintain design operation. This is imitation learning from a rule-based expert, not learning an unconstrained optimal control policy directly from reward.

build_dataset.py deduplicates states, separates contiguous 10-second blocks into train/validation, balances rare rules by limiting the design-hold share, and constructs system/user/assistant chat turns using prompt_format.py. A block split reduces immediate-neighbor leakage but is weaker than withholding entire independent operating scenarios. The code checks exact prompt overlap and train/serve formatting.

build_observer_dataset.py retains v2 proposal examples and creates v3 telemetry with longer windows, all flows and command/position pairs. It combines recorded faults with synthetic corruptions of real windows and holds out selected scenarios/channels/fault shapes. build_clean_observer_dataset.py later strengthens the split using whole recordings and exact deduplication, recording assignments/hashes in observer_clean/manifest.json. Original v2/v3 datasets remain historical evidence for their respective trained weights.

@figure training.png | Training and release lifecycle. Operator fine-tuning and telemetry forest training are separate paths with different labels and deployment artifacts.

train_local.py fine-tunes Qwen2.5-1.5B with 4-bit base loading and rank-16 LoRA adapters. Batch size one with gradient accumulation eight reduces GPU memory pressure. Gradient checkpointing trades computation for memory. The configured run uses one epoch, learning rate 2e-4 and response-only training. Training, evaluation and export are selectable stages. The export merges adapters to a temporary 16-bit model, converts it with llama.cpp and quantizes to GGUF q4_k_m for Ollama.

The notebook scram_observer_finetune.ipynb is the observer training route. Its cells include setup, dataset/format checks, training, evaluation and export; the source companion preserves code-cell order. It defaults to clean input data and a separate candidate destination. A notebook is executable state: re-running a later cell with stale earlier variables can differ from a clean top-to-bottom run.

Modelfile tells Ollama which GGUF to load and supplies model/template parameters. adapter_config.json describes LoRA/base-model configuration; tokenizer.json maps text pieces to token IDs; tokenizer_config.json and chat_template.jinja control tokenization/chat rendering. These are model infrastructure data, not custom process-control algorithms. GGUF and safetensors weights are local/ignored and not included in a normal Git checkout.

train_physics_telemetry.py constructs physical features, trains tree ensembles and selects using validation metrics, then exports numerical tree arrays to JSON. Training depends on numpy/scikit-learn; deployed inference does not. evaluate_physics_holdout.py verifies a frozen candidate and scores a sealed holdout against declared criteria. Editing the feature code or model without renewed evidence invalidates the release hashes. Reproduction scripts can overwrite default outputs, so study them before using the release directories as experiment destinations.

benchmark_command_parser.py performs non-actuating language-command replay. eval_ollama.py compares deployed operator behavior with labels and probes. benchmark_observer.py and benchmark_physics_pair.py preserve prompt hashes/raw responses to compare pipelines. They are evaluations, not hidden startup tasks. Saved metrics should always be read with the dataset, model identity, sampling protocol and exact scorer that produced them.

## 14. Experiments: what each family demonstrates

adversarial_policy.py scripts individually plausible target ramps. adversarial_eval.py compares no enforcement, measured-monitor-only and SIS interlock conditions under compromised instrumentation, then sweeps pressure thresholds. eval_plant.py provides research-only counterfactual paths; it is intentionally different from production PlantService and should not be used to infer that the live interlock has a bypass switch.

level_drift_eval.py studies a level transmitter drifting high while true inventory falls. level_frontier.py sweeps low-level trip thresholds and records preserved inventory margin versus time spent protected. The accelerated 40% demo threshold differs from the production 5% low limit. frontier_results.csv and level_frontier_results*.json are saved observations, not universal plant constants.

provenance_demo.py exercises unsigned, forged, replayed and direct-socket writes. capstone_demo.py combines command-origin attacks and level drift. llm_operator_demo.py swaps the brain among real Ollama and a clearly labeled scripted stand-in to show that enforcement should not depend on intelligence or honesty of the brain.

observer_v3_live.py, observer_split_live.py and observer_v3_proposal_replay.py test format parity, routing, model residency and proposal suitability. demo_rehearsal.py executes the demonstration sequence through Engine; safe_hold_tune.py explores recovery settings. Their JSON companions preserve historical runs. They commonly reset/write the simulator; reading these files does not require executing them.

## 15. Tests and verification strategy

The test suite is executable documentation. Unit tests construct PlantState/targets, fake clients and fake model responses, then assert invariants. This lets you learn a component without Docker or model weights. tests/test_interlock_truth.py is different: its two integration tests reset and manipulate a real running simulator, so the normal offline command excludes it.

test_safety_unit.py and test_quality_hardening.py cover latching, reset bands, sensor loss, combined hazards and races. test_provenance_gate.py tests tampering/replay/readback/NaN. test_keel.py covers rate/load-line/divergence/authority/slew/log behavior. test_l1_regulator.py covers directions, holding and bumpless transfer. test_governance.py and test_sequential_monitor.py explain corroboration, drift and recovery.

Observer format/dataset/wrapper tests check formatting parity, no truth leakage, command/sample pairing, invalid outputs, stale evidence and fallback. test_physics_detector.py verifies release hashes and advisory routing. Parser/studio tests cover action grounding, partial failures, E-stop, reset and concurrent rescue cancellation. Review/workspace regression tests cover previously found edge cases and session state.

frontend_smoke.cjs uses Playwright to exercise browser behavior with local test endpoints, including disconnected states and hostile markup. scripts/check_simulator.py launches an isolated compiled simulator on a temporary port and tests framing/input behavior. .github/workflows/quality.yml runs offline Python tests, JavaScript syntax checks, browser smoke checks and a Linux C++ compile with warnings treated as errors.

Commands recorded in the repository include python -m pytest -q --ignore=tests/test_interlock_truth.py and node tests/frontend_smoke.cjs after browser dependencies are installed. This documentation generation validates the documents and source coverage; it does not claim a fresh full application/test/inference run. Historical test counts in accuracy reports are historical evidence.

## 16. Relearning walkthroughs

Normal operation: begin with pressure around 2700 kPa, level around 44.2%, production around 100 kmol/h and A fraction near 0.47. The brain asks for design setpoints; L1 produces a small purge opening and normal feed/product lineup; Keel allows or slew-limits; the signed command passes the gate; no interlock trip exists. Observe that the UI displays both requested intent and downstream protection decisions.

Level-drift thought experiment: ordinary measured level drifts upward. The controller believes the vessel has too much liquid and opens product. Actual inventory falls. Measured-only detection can miss a sufficiently plausible drift, but Keel compares it with the independent channel and loses authority. If true level reaches the configured low trip, L0 closes product. When studying the trace, compare measured level, safety level, requested product command, actual product position and protection mode separately.

Forged-command thought experiment: an otherwise reasonable valve command has no valid HMAC. The gate rejects it even if pressure and level are safe. A valid signature with a reused sequence is also rejected. This proves origin/freshness protection, not physical safety. In contrast, a stolen-key command can pass admission and still be overridden by the interlock when the safety channel reaches a trip.

Slow-proposal attack: each pressure target step is below a one-step alarm threshold. SequentialMonitor accumulates same-direction intent and Governor reduces authority after corroboration. Because it examines pre-blend intent, the attack cannot disappear merely because governance has already damped the output. Keel independently watches physical trends; telemetry LLM labels do not decide the safety outcome.

Studio command: 'set feed 1 to 40% and close purge' goes through model parsing, clause grounding, ordered action execution and the studio's write lock. It does not go through the control-room command router, Keel or provenance gate. A subsequent 'save the reactor' starts Rescue. E-stop during inference cancels the run and prevents that late result from writing valves.

## 17. Misleading comments, historical code and practical boundaries

ARCHITECTURE.md describes early ownership and open items that no longer match current wiring: governance and real L1 now exist, composition conversion is implemented, Keel uses the safety channel, and secondary hazards can change protection while retaining first-cause diagnosis. Some module docstrings also describe older routing. Prefer the current engine and tested implementation when these disagree.

The code is a simulation control prototype, not a certified safety system. Network access to port 55555, signing-key integrity, service availability and the assumed independent safety channel define its trust boundary. Loopback publishing reduces exposure; it does not add authentication to the simulator protocol. A broken plant connection can stop corrective writes, and polling intervals are not hard deadlines.

Source presence does not imply execution: local Dockerfile, prebuilt image, historical reference simulator and evaluation fork are separate. Raw models and training environments can exist locally without being tracked. A hash-gated model cannot be edited casually without invalidating evidence. Cleaning artifacts or normalizing line endings can also invalidate hashes, which is why .gitattributes preserves evidence bytes.

When you next change code, follow the boundary: contract changes affect all layers; prompt changes affect datasets and serving; physical constants affect simulator/features/validation; control changes affect PI/Keel/tests; frontend changes affect DOM IDs, polling and browser tests. Keep the smaller tests closest to the changed contract in mind.

## 18. Glossary and a suggested reading schedule

Setpoint: desired process value. Actuator: device that changes the process, here a valve. PI: proportional plus integral feedback control. Anti-windup: prevent unusable integral buildup at saturation. Bumpless transfer: seed control state to avoid a jump when switching modes. SIS: the simulated independent safety-instrumented channel and protection policy. SCRAM: a protective trip. Hysteresis: different trip and reset thresholds to prevent chatter.

Provenance: command origin/authenticity. Replay: resubmitting an old authenticated command. HMAC: shared-secret message authentication code. CUSUM: cumulative evidence of sustained directional change. Authority: permission budget for AI deviation. GGUF: a model-serving weight format. LoRA: small trainable adapter matrices. QLoRA: low-bit base-model fine-tuning using adapters. Holdout: examples withheld from model selection. Leakage: information from evaluation entering training or prediction features.

Session one: contracts, startup and one cycle. Session two: L1, Keel, safety and provenance, with their unit tests. Session three: policy, observer formats, governance and model routing. Session four: frontend/API and studio differences. Session five: data collection, training, evaluation, simulator variants and packaging. After each session, explain one walkthrough aloud using the relevant source locations rather than memorizing class names.

## 19. Complete file-by-file catalogue

The following section is generated from the inspected snapshot. Every tracked path appears once as a file entry. Descriptions explain its role; Python symbols include line ranges so you can find the corresponding implementation in the source companion. Data schemas list top-level fields, not every repeated record or learned numerical parameter. Generated source maps are explicitly structural and do not override the current-runtime distinctions explained above.
