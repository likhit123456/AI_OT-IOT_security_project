# Final frontend — Glass monochrome

The design keeps the dashboard grouping, spacing and contextual data exploration of the earlier reference set, and replaces the Nocturne theme with a flat, monochrome, editorial layout with restrained glass accents. The canvas is near-black (or off-white in light mode) with a very faint neutral ambient glow. Sections are separated by hairline rules and whitespace instead of boxes. Colour is reserved for operational state: green, amber and red appear only for armed, disturbed and tripped conditions.

The interface is a React 18 and TypeScript application built with Vite, styled with Tailwind CSS and shadcn/ui-style primitives (Radix tabs, slider and slot), with Sonner for toasts. Source lives in `frontend/`. `npm run build` writes the bundle into `live/static/`, and the existing Python control-room server serves it unchanged, so running the app needs no Node tooling. The backend API (`/api/state`, `/api/action`) is the same one the previous interface used.

The Overview opens with a large plant-status headline (normal operation, disturbance active, safety trip, starting up or disconnected), followed by four readouts: pressure, inventory, product flow and effective authority, each with a small sparkline and the gap against the independent safety channel. A single process chart switches between pressure and level, with 1 min, 5 min, 15 min and all-time windows, a Zoom or Limits vertical scale, hover readings, an optional setpoint overlay, dotted trip limits and shaded tripped periods. Zoom fits the scale to the data so small disturbances are visible, and a trip limit that falls outside the visible range is labelled at the chart edge. Protection layers and actuator positions sit side by side below, then the live plant view and the event log. The 3D plant loads on request because of its download size and stays mounted when other tabs are open, so switching tabs never reloads it. The app has four pages, Overview, Operator, Attacks and Telemetry, reachable from the top navigation, the mobile tab bar, or the 1 to 4 keys.

The telemetry page adds Charts and Data views, minimum, average and maximum readings over the retained window, setpoint and safety-limit overlays, and CSV export. The table shows the newest 120 samples; export includes every retained sample with 13 columns, including the safety-channel values. Chart settings and export make no plant writes. The previous contextual signal inspector and expandable process instruments were removed in this redesign. Connection loss shows a Disconnected status and dims the readings, and the interface refuses control actions with a toast while telemetry is stale.

The Operator page holds the chat, suggested commands, brain switch and authority slider. The Attacks page lists the five scenarios, a custom sensor fault, the stolen-key lineup and recovery controls. Destructive actions (stolen-key takeover and plant reset) require a 1.2 second press and hold. Rejected actions appear as toasts. Existing model routing, plant commands, interlocks and authority rules are unchanged. The separate direct-control studio is not part of this redesign and keeps its Nocturne styling.

The visual treatment uses frosted glass on the navigation capsule, segmented controls, readouts pane, plant frame, buttons, inputs, chart tooltip, toasts and the top and bottom bars, which become translucent once the page scrolls. A soft glow behind the headline is neutral in normal operation and turns amber or red with the plant state. Type uses SF Pro on Apple devices and a bundled Inter variable font elsewhere, with tabular figures for readings. Light and dark follow the system setting and can be toggled, and the switch fades instead of snapping. Hover lighting applies only on devices with a precise pointer.

Motion follows one rule: readings and charts never animate, and everything else must have a purpose. The Overview staggers in on first load and the headline rises from a mask. Status changes crossfade with a slight blur, and a thin line draws across the top of the screen for amber and red states. Navigation indicators and segmented thumbs slide with transforms only. Page changes slide in from the direction of travel, except when switching with the keyboard, which is instant. Buttons scale to 0.97 on press, status dots ripple once when they turn amber or red, new chat messages and events rise in, and a rejected action shakes its notice. Reduced-motion preferences replace movement with a short fade. Disabled controls have no motion.

## References

- [Front workload dashboard](https://mobbin.com/explore/screens/cec3ccca-064b-447b-b15e-268bc27b1dbf): navigation, filter treatment and chart hierarchy.
- [Midday overview](https://mobbin.com/explore/screens/fa47d11e-0857-4ddc-a515-c837358b7c68): spacing, typography and metric emphasis.
- [Databricks interactive dashboard](https://mobbin.com/explore/screens/4a92dd62-1199-4288-9d26-a0f2c4c796c2): charts/data organization, contextual inspection and display options.

## Build

Edit files under `frontend/src/`, then run `npm install` once and `npm run build` inside `frontend/`. The build replaces `live/static/index.html` and the hashed files in `live/static/assets/`, which must be committed together because the browser test and the server use the built files. `npm run dev` serves the interface on port 5173 and proxies the API to a running backend on port 8765.

## Validation

`node tests/frontend_smoke.cjs` uses intercepted requests, so it never controls the live plant. Checks cover status and metric rendering, pressure/level switching, hover readings, time windows, display-only chart settings, keyboard page switching, 3D plant persistence across tabs, chat and scenario dispatch, sensor-fault and hold-to-confirm actions (including that a short press does nothing), data rows, CSV contents, safety-trip status, reconnects, stale telemetry, server restarts and both interfaces at desktop, tablet and mobile widths with no horizontal overflow. Screenshots and a sample CSV are saved under `artifacts/frontend/`.

The main dashboard is also checked against the running backend. The manual studio remains a separate application; do not run it alongside another plant writer. Running the app needs no new dependencies because the built bundle is committed; editing the interface requires Node and the packages listed in `frontend/package.json`. Fonts are bundled, with system fallbacks.

Previous frontend backups and generated design exports were permanently removed
during the final upload cleanup. The current implementation and this design
record are retained; browser checks can regenerate screenshots.
