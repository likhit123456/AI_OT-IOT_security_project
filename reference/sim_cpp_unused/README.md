# Shelved reactor implementation

These C++ files are a historical custom reactor implementation with fault
injection and a measured/truth split. They are retained for reference and are
not compiled or launched by the current Compose configuration or control room.

The active application uses the prebuilt GRFICS Docker simulator through the
Python plant adapter. The Python stack handles the simulation's measured/truth
separation used by SCRAM. Local simulator fixes and the isolated compiler check
use [`simulation/simulation/`](../../simulation/simulation/), not this folder.

See the [simulator README](../../simulation/README.md) and
[project README](../../README.md) for supported setup instructions. Changes here
do not change the running plant.
