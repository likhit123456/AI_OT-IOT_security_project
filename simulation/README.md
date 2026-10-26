# GRFICS simulator in SCRAM

The normal SCRAM application uses the prebuilt `fortiphyd/grfics-simulation`
image configured in [docker-compose.yml](../docker-compose.yml). Run these
commands from the repository root with Docker running:

```sh
docker compose up -d simulation
docker compose ps
docker compose logs simulation
```

The plant TCP endpoint is `127.0.0.1:55555`; the image's web visualization is at
`http://127.0.0.1/`. The main SCRAM UI is launched separately with `python -m live`
at port 8765. See the [project README](../README.md) for Python setup.

Compose runs only the simulator service, using the simulator-only supervisor
configuration in `docker/`. It does not launch the original full GRFICS lab.
The plant ports are unauthenticated and published on loopback.

## Compile and check the bundled C++ source

The source under `simulation/` contains the local solver, composition-delay and
TCP framing fixes. On Ubuntu or WSL, from the repository root:

```sh
sudo apt-get update
sudo apt-get install build-essential libjsoncpp-dev liblapacke-dev
mkdir -p artifacts/simulator
g++ -std=c++17 -Wall -Wextra -Werror simulation/simulation/main.cc simulation/simulation/TE_process.cc -ljsoncpp -llapacke -lpthread -o artifacts/simulator/simulation
python3 scripts/check_simulator.py artifacts/simulator/simulation
```

The checker launches an isolated simulator on a temporary port. Compiling this
binary does not replace the prebuilt image or establish equivalence with its
extended fault-injection behavior. Compose has no local build configured.

The local source now uses `b[2]` for the design purge. The Python reset helper
still applies the design purge explicitly because normal startup uses the
prebuilt image. To reset the running demonstration, use its UI reset action or
restart `python -m live` without `--no-reset`; this changes plant state.

## Visualization and provenance

The Unity build payloads are not included in this Git checkout. The runnable
visualization comes from the Docker image; local HTML/loader files alone are
not a complete Unity build. The older VirtualBox/GRFICSv2 installation steps do
not describe this repository's setup.

GRFICS attribution is recorded in [README_GRFICS.md](../README_GRFICS.md), with
the inherited license in [LICENSE](../LICENSE).
