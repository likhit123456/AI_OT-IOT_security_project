# Shared demo observer

This is the default L2 observer for proposal and telemetry review. The actual
F16 GGUF belongs at `app-deployment/scram-observer-demo-f16.gguf`; its expected
SHA-256 is recorded in `app-deployment/deployment.json`. It is approximately
994 MB and excluded from Git by the project-wide model-weight ignore rules.
Preserve or distribute it separately when preparing another checkout.

The default `python -m live` startup verifies the model and its saved development
validation evidence, starts the local llama.cpp server and enables measured
evidence review. `RUN_SLM_CONTROL_ROOM.cmd` uses the same model. The runner's
current Windows/CUDA prerequisites are described in [model setup](../README.md).
`SCRAM_OBSERVER_MODE=none` explicitly runs CPU/rules only.

The observer is for a simulation demo. Its combined decision accuracy on new,
independent recordings is not established. Prior model-only and development
results must not be treated as a new independent system evaluation. See
[current status](../../CURRENT_STATUS.md).
