# TE reactor operator sim

A clean manual-control station for the live GRFICSv3 Tennessee-Eastman plant.
There is no interlock, provenance gate, monitor or attacker: valve moves go straight to the simulator.
It is separate from the SCRAM security work and imports nothing from it except `l0_plant/grfics_client.py` and `scripts/reset_plant.py`.

```bash
docker compose up -d simulation      # or let the app start it
python -m sim_app                    # http://127.0.0.1:8770  (--port, --no-browser)
```

## API

| Endpoint | What it does |
|---|---|
| `GET /api/state?since=<seq>` | Returns pressure, level, flows, purge composition, valve positions, commanded setpoints, `e_stop`, `ruptured`/`exploded` (with time and pressure), peak pressure, and trend points after `seq`. |
| `POST /api/manipulate` | Body: `{"variable": "f1"\|"f2"\|"purge"\|"product", "delta": 1}` or `{"variable": ..., "value": 40}`, or `{"variable": "e_stop", "value": 1}`. Locked (409) once ruptured. |
| `POST /api/reset` | Restarts the simulator (`scripts/reset_plant`), waits for it to settle at the operating point, and clears the rupture. |

The simulator's only manipulated inputs are the four valve setpoints and `e_stop`.
It has no coolant input and no temperature state, so over-pressure is the failure mode.

## How GRFICS represents the explosion

The C++ simulator has no explosion state; it only clamps pressure at 3200 kPa (`TE_process.cc`).
The explosion lives in the Unity 3D client.
Its `TenEastSimulationConnector` polls pressure and calls `ExplosionManager.Explode` once pressure crosses `explosion_threshold`.
It then latches `exploded` and plays `PostExplosionEffects`: the reactor dome blows off, the vessel is breached, steam and smoke vent, the camera shakes and the screen glass cracks.

Measured on this image:
- The vessel is intact at 3087 kPa and ruptured by 3122 kPa; the app's flag fires at 3100 kPa.
- It stays ruptured when pressure falls again, even down to 2700 kPa.
- Only a simulation reset clears it.

The backend mirrors that latch (`RUPTURE_THRESHOLD_KPA`).
Opening both feeds with product shut ruptures the vessel in about 100 s.

## AI operator (`sim_app/ai.py`)

Ollama must be running with `qwen2.5:1.5b` and `scram-operator`.
No interlock or enforcer is involved.

**Typed commands** (`POST /api/command {"text": ...}`). One command can hold several actions, up to 8, such as "open feed 1 to 40% and cut purge to 0" or "shut the product valve then save the reactor".
- The actions run in the order written, each exactly like a manual move, and each reports its own outcome. One refused or unrecognised action does not stop the others.
- The response lists `actions[]`; the old single-action fields (`parsed`, `result`) still describe the first action.
- The page queues commands, so the next one can be sent while the AI is still reading the last. They run in the order sent.
- The stock `qwen2.5:1.5b` turns the text into one JSON action, `{action: adjust|set|rescue|stop|unknown, valve, amount}`, constrained by a JSON schema.
- The action goes through the same path as the manual buttons and is clamped to 0–100%.
- A small grammar check settles "to N" (set) versus "by N" (adjust), and the sign for close/cut/reduce, because the 1.5B model sometimes mixes these up. Any correction is shown in the activity panel.
- Unrecognised text does nothing and returns 422.

**Save the reactor** (`POST /api/ai/rescue`, the button, or a command such as "save the reactor").
- Every 2 s the trained `scram-operator` reads the measured state and emits setpoints. It uses `OllamaOperator` with the same prompt format, parsing and envelope clamp it was trained with.
- `regulate()` turns those setpoints into valves:
  - pressure above setpoint: purge opens, feeds are cut, product opens a little;
  - pressure below setpoint: purge shut, feeds raised slightly;
  - feeds follow the production setpoint from the design valve lineup;
  - product follows level.
- It never drives the feeds from product flow, which is the `l1_stub` feed-cut bug.
- The loop stops after 3 consecutive decisions within 30 kPa and 2% of the design point. It then leaves the valves at the design lineup and returns to manual.
- `POST /api/ai/stop` (Stop AI) hands control back at any time.
- Manual moves are refused while the AI is in control.

Tested: from 3018 kPa and rising with both feeds open and product shut, pressure peaked at 3025 kPa. It was back near design (2717 kPa) in 35 s and settled at 2702 kPa.

## GRFICS steady-state purge bug (worked around)

`TE::steady_state()` in `simulation/simulation/TE_process.cc` sets the purge valve from `b[3]`, one past the end of its three-element solution array.
A fresh simulator therefore starts with the purge at about 0% instead of its design opening of 0.393%.

Feed 1 carries 0.5% inert B, and B can only leave through the purge.
With the purge shut, B builds up and pressure creeps about 3 kPa/min at the "design point", faster with more feed 1.

`scripts/reset_plant.py` now writes the correct design purge (`DESIGN_PURGE_VALVE_PCT`) after every restart and confirms it, retrying while Docker's port proxy drops early writes.
The AI regulator holds that purge at its pressure setpoint.
Measured live, the pressure drift went from +3.2 kPa/min to 0.0 kPa/min.
