# SCRAM control room frontend

This React, TypeScript, and Vite app is the main SCRAM control room. The checked-in
production files in `../live/static/` are served by `python -m live` on port 8765.
Node.js is only needed when developing or rebuilding the frontend.

## Development

From this directory:

```powershell
npm.cmd install
npm.cmd run dev
```

Open http://127.0.0.1:5173. Start the backend separately from the repository root
using the main README's instructions. Vite proxies the API and the optional
GRFICS reactor view to http://127.0.0.1:8765.
The development server uses a fixed loopback port; it fails if 5173 is occupied.
Keep the proxy's original Host header so action Origin validation succeeds.

## Build and validation

```powershell
npm.cmd run build
```

The build typechecks the source and writes the production HTML, JavaScript, CSS,
and local fonts to `../live/static/`. Commit the source and rebuilt assets together.
The build preserves `reactor.html`, which the backend uses for the 3D plant view.
The older control-room scripts and styles are retained during this migration;
the new index page only loads the React assets.

From the repository root, run the isolated browser checks with an installed
Playwright package and Chromium:

```powershell
node tests/frontend_smoke.cjs
```

Set `PLAYWRIGHT_MODULE` to an existing Playwright package when it is installed
outside the repository. The checks intercept API and reactor requests and do not
connect to the simulator. They cover navigation, charts, action payloads, hold
controls, CSV export, responsive layouts, disconnects, stale telemetry, and restarts.

## Operator and attack recovery

Baseline rules accept explicit setpoint requests as well as manual valve commands.
Model selection reports unavailable models without replacing the active brain.
Mixed valve instructions keep each clause's own value (for example, feeds at
100% and purge closed). The authority slider commits once when released.

Sensor fault severity follows the selected mode: signed units per hour for drift,
nonnegative amplitude for noise, and a fraction from 0 to 1 for dropout. Frozen
readings require no severity. Actuator faults can target each valve independently;
changing a valve's fault mode replaces its previous staged fault.

During a disconnect or stale telemetry, operating commands pause while recovery
controls remain available. Clear faults stops scenarios and invalidates old model
instructions. A full plant reset starts a new chart session and cancels commands
queued for the old run. Re-arm is available only inside the SIS recovery band.
