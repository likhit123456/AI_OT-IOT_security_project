# SCRAM

An AI-supervised chemical-plant simulation with layered governance, deterministic
control, command provenance, and an independent simulated safety channel.

The main application is the SCRAM control room in `live/`. The separate reactor
studio in `sim_app/` is a direct-control sandbox. See
[QUALITY_IMPROVEMENTS.md](QUALITY_IMPROVEMENTS.md) for the reliability pass,
[ACCURACY_IMPROVEMENTS.md](ACCURACY_IMPROVEMENTS.md) for later detector results, and
[ARCHITECTURE.md](ARCHITECTURE.md) for the original design and trust assumptions.

## Run the control room

Requires Python 3.11+, Docker Desktop, and the GRFICS simulation image. Ollama
and the trained models are optional; the operator falls back to deterministic
baseline control when its model is unavailable.

Run the commands below from the repository root. Custom LLM weights are excluded
from Git; see [model setup](model/README.md) for required files and registration.
For an explicit model-free operator, use `python -m live --brain baseline`.

```powershell
python -m pip install -r requirements.txt
docker compose up -d simulation
python -m live
```

Open http://127.0.0.1:8765. Normal startup resets the simulator to its design point.
To attach without resetting, use `python -m live --no-reset --no-browser`.
The plant's unauthenticated ports are published on loopback only.

Ollama configuration is no longer changed on ordinary startup. Explicit setup
is available with `python -m live --configure-ollama`. That option can persist
`LLAMA_ARG_CACHE_RAM=0` and restart Ollama; see `model/README.md` for the reason.

## Components

| Layer | Implementation | Role |
| --- | --- | --- |
| L3 | `l3_brain/` | Local LLM or baseline emits supervisory setpoints |
| L2 | `monitor/` | Rule monitors, proposal observer, telemetry observer, governance |
| L1 | `l1_regulator.py`, `enforcer/keel.py` | PI regulation, deterministic command constraints, final signing |
| L0 | `l0_plant/` | Plant adapter, provenance gate and latched protective actions |
| UI | `live/`, `sim_app/` | Full control room and separate manual studio |
| Data/evaluation | `training/`, `model/`, `experiments/` | Collection, training, artifacts and reproducible evaluations |

Three local LLMs serve the full stack: `scram-operator`, `scram-observer` (proposal
judgments), and `scram-observer-v3` (telemetry). A release-gated 236 kB CPU
physics detector corrects advisory telemetry verdicts and preserves the original
LLM label. It never contributes to authority or protective actuation. Its model,
features and evaluation evidence are checked by hash before loading. See
[accuracy improvements](ACCURACY_IMPROVEMENTS.md) for measured results and remaining gaps.

Measured composition values are fractions; composition setpoints are percentages.
The `truth` field carries the independent simulated safety channel for L0/Keel
and evaluation. The AI and ordinary behavior monitor consume measured signals.

Trips retain their first cause while continuing to evaluate secondary hazards.
Combined pressure and low-inventory protection shuts feeds, opens purge, and
closes product. Safety-sensor loss latches E-stop. The live demonstration may
automatically rearm after ten clean seconds, with a repeated-trip lockout; this
is separate from the base interlock's explicit reset API. These are simulation
policies, not certified hardware safety guarantees.

## Manual reactor studio

```powershell
python -m sim_app
```

Open http://127.0.0.1:8770. This path writes directly to the simulator and has no
SCRAM interlock or provenance gate. Its stock `qwen2.5:1.5b` model parses commands;
the trained operator runs rescue. E-stop cancels rescue, blocks later AI valve
writes, and requires deliberate release. Avoid running two plant writers at once.

## Verify and reproduce

```powershell
python -m pip install -r requirements-dev.txt -r requirements-eval.txt
python -m pytest -q --ignore=tests/test_interlock_truth.py
```

To reproduce the earlier restricted-classifier experiment, use a separate
checkout: these commands regenerate datasets and overwrite model/evaluation
outputs. The current runtime prefers the [physics detector](model/telemetry-physics/README.md).

```powershell
python -m training.build_clean_observer_dataset
python -m training.train_telemetry_classifier
python -m training.evaluate_telemetry_specialist
```

For fresh local Ollama inference with whichever specialist is currently approved:

```powershell
python -m training.benchmark_observer --per-class 12 --output artifacts/observer-benchmark-new.json
```

This currently selects the physics detector, so it does not reproduce the older
restricted-classifier comparison exactly. The separate output path preserves
the saved historical benchmark. New clean data lives under `training/observer_clean/`;
its whole-recording splits, duplicate exclusions and hashes are recorded in a
manifest. The observer notebook defaults to these inputs and exports a separate
candidate instead of overwriting the deployed LLM.

For browser checks, install Node.js (CI uses version 22), then run:

```sh
npm install --no-save playwright
npx playwright install chromium
node tests/frontend_smoke.cjs
```

On Linux, CI uses `npx playwright install --with-deps chromium` to install browser
system dependencies too. GitHub Actions defines offline, browser and Linux
simulator checks. The two tests in `test_interlock_truth.py` reset and manipulate
a real running simulator, so they are excluded from the ordinary offline run.

The bundled C++ source can be compiled and checked on Linux/WSL:

```sh
g++ -std=c++17 -Wall -Wextra -Werror simulation/simulation/main.cc simulation/simulation/TE_process.cc -ljsoncpp -llapacke -lpthread -o simulation-test
python3 scripts/check_simulator.py ./simulation-test
```

Install `libjsoncpp-dev` and `liblapacke-dev` first. This isolated test starts a
separate process on a temporary port. Compose continues to use the prebuilt
GRFICS image: local source fixes do not replace its binary automatically.

`l1_stub.py`, `run_demo.py`, and `reference/` are historical/minimal paths. Use the
full control room for the current integration. GRFICS is GPL-3.0; this project
retains its license and attribution.

## Repository contents

Source, datasets, CPU models and evaluation reports are included. LLM weights,
training environments, runtime logs and generated browser output are ignored.
Obsolete local backups and caches were removed during upload cleanup. Use Git
to publish the project so these ignore rules apply. JSON/JSONL model and dataset
evidence retains its exact bytes across checkouts for hash verification.
