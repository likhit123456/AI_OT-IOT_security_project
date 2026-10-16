# SCRAM — layered architecture & contracts

SCRAM runs an **AI operator** on a simulated chemical plant, with a
**governance layer** that dynamically reduces the agent's authority and an
**independent hardware safety interlock** underneath that the AI cannot
override. The plant is the open-source GRFICS v3 Tennessee-Eastman reactor
(the model only — the OT-lab scaffolding is scrapped).

## Layers (Purdue / ISA-95 mapping)

| Level | Role | Owner |
|------|------|-------|
| **L3 Brain** | AI operator. `decide(state) -> SupervisoryTargets`. Emits *intent* (setpoints), never valve positions. | **you** |
| L2 Supervisory | Monitoring, Guardian (attack detection/isolation), HMI. | team |
| L1 Control | Regulatory PI loops: `regulate(targets, state) -> ControlCommand` (valve %). | team |
| **L0 Plant** | GRFICS TE model + socket service + **independent safety interlock (SCRAM)**. | **you** |

Control flows top-down; state flows bottom-up:

```
  L3 decide(state) ──▶ SupervisoryTargets
        │ governance clamps authority (L1/L2)
        ▼
  L1 regulate(targets,state) ──▶ ControlCommand (valves)
        ▼
  L0 apply(command) ──▶ PlantState (measured) ──▶ back up to L2/L3
```

The **only** thing L3 outputs is `SupervisoryTargets`. The **only** thing L0
accepts is a `ControlCommand`. Those two contracts are the seams your teammates
build against — see `contracts.py`.

## The safety thesis (why it's called SCRAM)

`l0_plant/safety.py` is an interlock that watches measured pressure/level and
**latches a trip** on a hard-limit excursion, forcing feeds shut and relief
paths open — regardless of what L3 intends or how much authority governance
granted. A latched trip can only be cleared by a deliberate reset once
variables are back inside the re-arm band. This is the last-resort physical
containment layer; the AI has no code path to override it.

## Repo layout

```
contracts.py          shared dataclasses: PlantState, SupervisoryTargets, ControlCommand
l0_plant/
  grfics_client.py    thin TCP/JSON client to the sim (port 55555)
  safety.py           independent SCRAM interlock (hard limits, latch, re-arm)
  plant_service.py    L0 boundary: raw JSON -> PlantState, enforces interlock
l3_brain/
  policy.py           swappable brain: BaselineOperator now; RL/LLM later
  operator.py         L3 loop + governance authority handshake
l1_stub.py            PLACEHOLDER for the teammate's L1 (PI loops) — replace
run_demo.py           wires L0 + L1stub + L3 end-to-end
docker-compose.yml     plant-only: runs the prebuilt GRFICS simulation image
reference/sim_cpp_unused/   shelved custom C++ reactor - not built, see ARCHITECTURE.md
```

See `ARCHITECTURE.md` for the full design (layers, contracts, the interlock thesis).

## Run it

```bash
docker compose pull simulation
docker compose up -d simulation    # plant on :55555 (JSON) + helm on :80
# in another shell, from repo root:
python3 run_demo.py
```

## Contracts your teammates need

**L1 teammate** implements `regulate(targets: SupervisoryTargets, state:
PlantState, dt: float) -> ControlCommand`. Consume the setpoint fields (any may
be `None` = hold), output absolute valve % in `[0,100]`. `l1_stub.py` shows the
actuator mapping and a working-but-crude PI baseline to replace.

**L2 / governance teammate** provides the `authority_fn: () -> float` that
`AIOperator` takes (0..1). Drop authority to throttle the brain; L3 blends its
setpoints toward "hold" as authority falls, and L1/governance enforces the hard
clamp. Detection logic must read only the `measured` fields of `PlantState` —
`PlantState.truth` is eval-only ground truth, not a production signal.

## Open items (for you / tuning)

- **Composition units**: sim reports `A_in_purge` as a fraction (~0.47); the
  baseline target is written as `47.0`. Reconcile units at the L1 seam.
- Baseline policy is intentionally dumb — it's the floor an ML/LLM policy beats.
- GRFICS v3 is **GPL-3.0**; reusing the TE model makes SCRAM GPL-3.0.

