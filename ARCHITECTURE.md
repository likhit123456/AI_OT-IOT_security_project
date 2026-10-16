# SCRAM — Architecture

SCRAM runs an **AI operator** on a simulated chemical plant. A **governance
layer** dynamically reduces the AI's authority, and an **independent safety
interlock** underneath it cannot be overridden by the AI.

## 1. Layers (Purdue / ISA-95) and ownership

| Level | Role | Code | Owner |
|---|---|---|---|
| L3 Brain | AI operator. `decide(state) -> SupervisoryTargets`. Emits setpoints (intent), never valve positions. | `l3_brain/` | **Me** |
| L2 Supervisory | Monitoring, Guardian (attack detection / isolation), HMI, governance authority. | — | Teammates |
| L1 Control | Regulatory PI loops. `regulate(targets, state, dt) -> ControlCommand`. | `l1_stub.py` (placeholder) | Teammates |
| L0 Plant | Plant client, fault-injection helpers, SCRAM interlock. | `l0_plant/` | **Me** |

Control flows down, state flows up:

    L3 decide(state) -> SupervisoryTargets
          | governance clamps authority (L1/L2)
    L1 regulate(targets, state) -> ControlCommand (valve %)
          |
    L0 apply(command) -> PlantState -> back up to L2/L3

## 2. Seams — `contracts.py` is the single source of truth

- `SupervisoryTargets` (L3 out): `pressure_sp`, `level_sp`, `production_rate_sp`,
  `a_comp_sp`, `authority` (0..1), `rationale`. `None` = hold.
- `ControlCommand` (L1 out, L0 in): four valve positions in % [0,100]. L0 clamps.
- `PlantState` (L0 up): measured process variables, valve positions, cost,
  `scrammed` / `scram_reason`, and `truth` (eval-only).
- Everything above L0 works in engineering units, never 0-65535 Modbus scaling.
- Contract changes are agreed with the teammates first; both sides build to them.

## 3. Plant

The plant is the **stock GRFICSv3 Tennessee-Eastman reactor, run from the
prebuilt Docker image** (`fortiphyd/grfics-simulation`; see
`docker-compose.yml`). Run:

```bash
docker compose pull simulation
docker compose up -d simulation
```

- **:55555** — line-delimited JSON over TCP.
  `{"request":"read"}\n` returns state; `{"request":"write","data":{"inputs":{...}}}\n`
  sets valve setpoints (`f1_valve_sp`, `f2_valve_sp`, `purge_valve_sp`,
  `product_valve_sp`), `e_stop`, and the per-channel fault write-fields
  (`<channel>_fault_mode` / `_fault_severity` — see `l0_plant/fault_injection.py`).
- **:80** — helm: the GRFICS dashboard with the 3D plant view.
- No custom reactor build is used. `reference/sim_cpp_unused/` holds an earlier
  modified C++ reactor, shelved in favor of this design.

## 4. Measured vs. truth

Each read from the reactor carries two parallel views of the same physical
state:

- **`outputs`** — the basic process channel. A dedicated safety-instrumented
  sensor path, independent of the basic control loop, tracking the true
  physical variables that path is wired to.
- **`measured`** — the same variables as seen through the plant's ordinary
  instrumentation, which `l0_plant/fault_injection.py` can degrade per channel
  (frozen, drift, noise, dropout) to exercise L1/L2/L3 against corrupted
  signals.

`PlantState` exposes `measured` as the live signal — that is what L1, L2 and
L3 consume. `PlantState.truth` carries `outputs`, for eval/scoring only; it is
never a production signal.

## 5. The SCRAM interlock thesis

`l0_plant/safety.py` is a last-resort protective layer under the AI. It
evaluates the **safety-instrumented sensor path** (`outputs`), not the basic
control system's `measured` view, and latches a trip on a hard-limit
excursion (pressure >= 3000 kPa, level <= 5% or >= 95%). On trip it forces the
plant into a **cause-specific safe state** (`SafetyInterlock.safe_state_inputs()`),
regardless of what L3 intends or how much authority governance granted:

| Trip cause | Safe state | Why |
|---|---|---|
| pressure high, level high | `e_stop` (feeds shut, purge and product open) | relieves pressure / drains an overfull vessel |
| level low | product valve closed; feed and purge valves held where they are (not written) | `e_stop` would open product and keep draining an already-low vessel; closing it lets inflow and reaction recover the level to the re-arm band |

The first trip cause latches; a second hazard while latched does not change the
safe state. A safety-channel reading that is missing or not a finite number trips
too (cause `sis_sensor_fault`, safe state `e_stop`): the SIS fails safe when it
cannot read its own sensor. A command with a non-finite valve value is never
applied (the gate rejects it; without a gate the valves are held).

- **Scope assumption:** this design assumes the safety-instrumented sensor
  path is out of the attacker's reach — standard SIS independence, the same
  assumption a real safety-instrumented system makes about its own dedicated
  sensors and wiring being separate from the basic process control system. The
  fault-injection helpers in `l0_plant/fault_injection.py` only ever touch the
  basic control system's `measured` view; they have no way to reach the SIS
  path, by construction of the reactor's own JSON schema.
- **Provenance scope assumption:** the command-origin gate (section 5a) assumes
  the signing key and the L1 signer are uncompromised, the same shape of
  assumption as SIS independence above. An attacker who can sign as L1 (steal
  the key, or run inside the signer) passes admission; the physics interlock is
  then the only remaining ground. Conversely, the two grounds fail
  independently: a compromised agent without the key is stopped at admission
  even when its commands are physically benign, and a compromised sensor
  cannot stop the SIS trip. Neither ground covers an attacker who has both the
  key and the SIS channel.
- Trips latch. Reset is deliberate (`PlantService.reset_interlock()`) and only
  succeeds once the SIS channel is back inside the re-arm band
  (pressure <= 2800 kPa, level 15-85%; the bands are `TripLimits` fields); it reads the SIS channel directly, not
  `PlantState`'s measured fields, so a degraded basic-control sensor can't
  unlock a reset either.
- The AI has no code path to override it.

Tests: `tests/test_safety_unit.py` is a fast, pure-logic unit test of
`safety.py` (trip / latch / override / re-arm, no container). `tests/test_interlock_truth.py`
is a slower integration test against the live container — it degrades the
basic-control pressure sensor and confirms the interlock still trips off the
SIS channel, latches, and refuses to clear until the SIS channel re-arms.
`experiments/level_drift_eval.py` is the low-level counterpart: a native drift
fault on the basic-control level transmitter, with a measured-only monitor vs.
the SIS interlock (it uses a test-only 40% low-level setpoint; the production
default stays 5%).

### 5a. Command-origin integrity (provenance gate)

`l0_plant/provenance_gate.py` is the second ground of the enforcer's veto
alongside the physics interlock: physics asks "is the process state safe?",
the gate asks "did the authorized path issue this command?". It covers the
case physics cannot: a command that is individually inside every physical
limit (the SIS never trips) but unauthorized. `PlantService(gate=...)` is
opt-in; with no gate every command is trusted (legacy behaviour).

- **Via the enforcer:** L1 signs each command (`CommandSigner`: HMAC-SHA256
  over a strictly increasing `seq` + the four valve positions, carried in the
  additive `ControlCommand.seq` / `auth_tag` fields). Unsigned, forged,
  tampered or replayed commands are rejected and last-good is re-asserted.
- **Around the enforcer:** the plant's :55555 protocol is unauthenticated, so a
  direct write can't be prevented from here. The enforcer is the only
  sanctioned writer, so each cycle it audits the plant's valve / `e_stop`
  readback against what it last wrote; any mismatch is an out-of-band write,
  is logged, and is overwritten by the next write. Detected and bounded
  (dwell <= one control cycle), not prevented - prevention needs network-level
  controls on :55555.
- **Seam note:** `seq` / `auth_tag` are a change to the L1 seam; the L1
  teammate must confirm it (L1 signs before handing to L0) before the gate is
  enabled outside experiments. `l1_stub.py` does not sign yet.
- Tests: `tests/test_provenance_gate.py` (fast); live demo
  `experiments/provenance_demo.py`.

### 5b. The LLM operator (L3) is untrusted by design

`l3_brain/policy.py` `LLMOperator` is a drop-in `Policy` (select with
`SCRAM_POLICY=llm|baseline`; `llm` without credentials falls back to
`BaselineOperator`). Server-side only; it reads measured `PlantState` fields,
never `.truth`, and emits setpoints + rationale, never valves. Its output is
parsed, validated and clamped to an `Envelope`; any failure (timeout, refusal,
bad JSON, no key) becomes "hold" (all setpoints None), and it calls the model
on a ~20 s supervisory interval from a background thread so L1 keeps
regulating every cycle. The enforcer's guarantees (sections 5, 5a) do not
depend on it behaving: `experiments/llm_operator_demo.py` reruns the capstone
attack with it as the brain.

Fine-tune path: `training/` collects real GRFICS states (`collect_states.py`),
labels them with a rule-based expert grounded in the sim's settled design point
and the SIS limits (`expert_policy.py`), and builds balanced chat examples
(`build_dataset.py`). The prompt format (system text + measured-state JSON) is
defined once in `l3_brain/prompt_format.py` and used by both the dataset and
`LLMOperator`, so a fine-tuned model is served exactly what it was trained on.
Trained outputs live in `model/` (weights gitignored).

The trained model runs as the brain via `OllamaOperator` (an `LLMOperator`
subclass; select with `SCRAM_OPERATOR=ollama`). It calls the local Ollama
server (`scram-operator`, `http://127.0.0.1:11434`, override with
`SCRAM_OLLAMA_MODEL` / `SCRAM_OLLAMA_URL`) with the same prompt_format, on a
5 s interval, and keeps the same parse/validate/clamp and hold-on-any-failure.
If Ollama or the model is unavailable at selection time, `make_policy` falls
back to `BaselineOperator`. Demo: `python -m experiments.llm_operator_demo
--brain ollama`, which writes `experiments/trained_operator_demo_results.json`
and logs each decision's measured view next to the truth.

## 5c. Live control room

`python -m live` runs the full stack continuously against the plant: the trained
operator (`OllamaOperator`, falls back to baseline) -> governance authority ->
L2 `BehaviorMonitor` -> L1 (`live/engine.py` `LiveL1`: the stub, bumpless-primed,
production by feedforward instead of the stub's mis-paired loop) -> signed ->
`PlantService(gate, limits)`. It serves http://127.0.0.1:8765 with the GRFICS 3D
reactor embedded, the AI's decisions (what it saw vs. the truth), trends, and
scenario buttons (level drift, pressure dropout, forged commands, out-of-band
write, stolen L1 key) plus clear-faults / reset-interlock / reset-plant. On
re-arm the plant restarts from the design valve lineup. Demo SIS limits by
default (40% low-level trip); `--production-limits` for the 5% default.

## 6. Repo layout

    contracts.py            shared dataclasses (the seams)
    l0_plant/                grfics_client, plant_service, safety, provenance_gate, fault_injection
    l3_brain/                policy, operator (authority handshake), prompt_format
    training/                real-state collection, expert labels, fine-tune dataset
    model/                   trained operator outputs (weights gitignored)
    live/                    the whole stack running live + control room (python -m live, :8765)
    l1_stub.py               placeholder for teammate's L1
    run_demo.py              L0 + L1 stub + L3 end to end
    docker-compose.yml       plant-only: simulation service, prebuilt image
    simulation/               simulation image context (web_visualization, remote_io)
    reference/sim_cpp_unused/   shelved custom C++ reactor (not used)
    tests/                   test_safety_unit.py (fast), test_interlock_truth.py (live)

## 7. Licence

GRFICSv3 is GPL-3.0. Reusing the TE model makes SCRAM GPL-3.0 (`LICENSE`).

## 8. Open items

- Composition units: reactor reports `A_in_purge` as a fraction (~0.47);
  baseline policy targets `47.0`. Reconcile at the L1 seam.
- Baseline L3 policy is intentionally dumb — it's the floor an ML/LLM policy beats.
- `l1_stub.py` is a placeholder; the teammate's real L1 replaces it wholesale.
- Governance / L2 (authority_fn, Guardian) doesn't exist yet; `AIOperator`
  defaults to full authority when none is supplied.
