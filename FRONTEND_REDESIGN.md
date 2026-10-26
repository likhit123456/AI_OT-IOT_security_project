# Final frontend — Nocturne

The design combines Front's clear dashboard grouping, Midday's spacing and typography, and Databricks' contextual data exploration. The current **Nocturne** theme uses a black canvas, faint orbital contours, smoky violet and teal illumination, and dark panels with subtly lit edges. The implementation uses the existing HTML, CSS and JavaScript stack.

The main overview now starts with pressure, inventory, product flow and effective authority. A primary process chart switches between pressure and level, with synchronized time windows and hover readings. Protection layers sit beside the chart; actuator positions and the live plant view follow. Expandable process instruments retain the original gauges.

The telemetry page adds Charts and Data tabs, a contextual signal inspector, minimum/maximum/average readings for the selected time window, optional setpoint and safety-limit overlays, and CSV export. The table shows the newest 120 samples; export includes every retained sample in the selected window. Chart settings and export make no plant writes. Current readings and historical window statistics are labeled separately. Connection loss marks values as last readings.

The operator console and separate direct-control studio share the black palette, typography and controls. Text, chart series, menus, tables and instrument fills are tuned for dark surfaces. Semantic green, amber and red remain reserved for operational state. Existing model routing, plant commands, interlocks and authority rules are unchanged.

The user-approved final design retains the black background, orbital artwork, violet/teal light, larger type and existing layouts. Most UI text is approximately 20% larger, with a minimum of 11.5 px for compact labels. Primary actions have gradient depth and a hover light sweep. Secondary buttons, navigation, chart tabs, scenario selectors, filters, suggested commands and valve adjustment controls now also have a light sweep, border illumination, hover lift and tactile press feedback. Keyboard focus gets a clear halo. Disabled controls have no motion or glow; reduced-motion preferences disable sweeps and movement. Emergency controls retain restrained red feedback. Mobile navigation stacks icons above labels to accommodate the larger type.

## References

- [Front workload dashboard](https://mobbin.com/explore/screens/cec3ccca-064b-447b-b15e-268bc27b1dbf): navigation, filter treatment and chart hierarchy.
- [Midday overview](https://mobbin.com/explore/screens/fa47d11e-0857-4ddc-a515-c837358b7c68): spacing, typography and metric emphasis.
- [Databricks interactive dashboard](https://mobbin.com/explore/screens/4a92dd62-1199-4288-9d26-a0f2c4c796c2): charts/data organization, contextual inspection and display options.

## Validation

`node tests/frontend_smoke.cjs` uses intercepted requests, so it never controls the live plant. Checks cover metric rendering, pressure/level switching, hover readings, shared time filters, expandable instruments, inspector selection, display-only settings, data rows, CSV contents, keyboard tabs, scenario dispatch, chat dispatch, safe-hold status, reconnects, stale telemetry, server restarts and both interfaces at desktop/tablet/mobile widths. Screenshots and a sample CSV are saved under `artifacts/frontend/`.

The main dashboard is also checked against the running backend. The manual studio remains a separate application; do not run it alongside another plant writer. No new framework or package dependency is required. Fonts have system fallbacks.

Previous frontend backups and generated design exports were permanently removed
during the final upload cleanup. The current implementation and this design
record are retained; browser checks can regenerate screenshots.
