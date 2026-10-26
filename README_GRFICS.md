# GRFICS background and attribution

SCRAM builds on the GRFICS chemical-plant simulation created by Fortiphyd Logic.
The original GRFICS lab combines process simulation and visualization with
industrial networking, PLC/HMI components and security-training tools.

This repository contains a SCRAM adaptation. Its current Compose file starts
only the prebuilt `fortiphyd/grfics-simulation` image. Kali, Caldera, engineering
workstations, PLC/HMI containers, routers and Wazuh from the broader lab are not
services in this checkout. Historical screenshots under `images/` illustrate
that broader lab and should not be interpreted as SCRAM's current interface.

## Run this repository

Follow [README.md](README.md) for the SCRAM control room and
[simulation/README.md](simulation/README.md) for simulator compilation and checks.
Do not apply old full-lab service credentials, VirtualBox network configuration,
or Compose line-number instructions to this simulator-only configuration.

The current local endpoints are:

| Component | Address |
| --- | --- |
| SCRAM control room | `http://127.0.0.1:8765` |
| Separate manual studio | `http://127.0.0.1:8770` |
| GRFICS visualization in Docker | `http://127.0.0.1/` |
| Plant TCP interface | `127.0.0.1:55555` |

Run only one plant writer at a time. The manual studio is separate from SCRAM's
governance, provenance gate and interlock path.

## Attribution and license

The inherited GRFICS license is [GPL-3.0](LICENSE). Original attribution is
retained in the source and license. Fortiphyd Logic's website is
<https://fortiphyd.com>. SCRAM's additional control, observer and evaluation
layers are described in [ARCHITECTURE.md](ARCHITECTURE.md) and the root README.
