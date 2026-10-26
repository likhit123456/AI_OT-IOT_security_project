// Databricks-inspired exploration of the existing read-only telemetry stream.
(() => {
  const byId = id => document.getElementById(id);
  const settings = {setpoints: true, limits: true};
  let mode = "charts", signal = "pressure", tableKey = "";
  const definitions = {
    pressure: {index: 2, truth: 3, target: 10, unit: "kPa", digits: 1},
    level: {index: 4, truth: 5, target: 11, unit: "%", digits: 2},
    authority: {index: 13, unit: "", digits: 2},
    valves: {index: 9, unit: "%", digits: 1},
  };
  const format = (value, digits = 1) => Number.isFinite(value) ? value.toFixed(digits) : "—";
  const time = value => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
  const samples = () => {
    if (!H.length) return [];
    const end = H[H.length - 1][1];
    return H.filter(row => row[1] >= end - WIN);
  };
  function selectSignal(value) {
    signal = value;
    byId("inspectorSignal").value = value;
    document.querySelectorAll("[data-signal]").forEach(card => card.classList.toggle("selected-chart", card.dataset.signal === value));
    document.querySelectorAll("[data-inspect]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.inspect === value)));
    if (S) render(S);
  }
  function setMode(value, focus = false) {
    mode = value;
    document.querySelectorAll("[data-explorer]").forEach(button => {
      const selected = button.dataset.explorer === value;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    });
    byId("explorer-chart-panel").hidden = value !== "charts";
    byId("explorer-data-panel").hidden = value !== "data";
    if (S) render(S);
    draw();
  }
  document.querySelectorAll("[data-explorer]").forEach(button => button.addEventListener("click", () => setMode(button.dataset.explorer)));
  document.querySelector(".explorer-tabs").addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    setMode(event.key === "Home" ? "charts" : event.key === "End" ? "data" : mode === "charts" ? "data" : "charts", true);
  });
  document.querySelectorAll("[data-inspect]").forEach(button => button.addEventListener("click", () => selectSignal(button.dataset.inspect)));
  byId("inspectorSignal").addEventListener("change", event => selectSignal(event.target.value));
  for (const [id, key] of [["showSetpoints", "setpoints"], ["showLimits", "limits"]]) {
    byId(id).addEventListener("change", event => { settings[key] = event.target.checked; draw(); });
  }
  function connection(ok, text) {
    byId("inspectorFreshness").textContent = ok && text === "live" ? "Current sample · live" : `Last reading · ${text}`;
    byId("inspectorFreshness").classList.toggle("is-stale", !ok || text !== "live");
  }
  function renderTable(rows) {
    const key = `${rows.at(-1)?.[0]}:${WIN}:${rows.length}`;
    if (key === tableKey) return;
    tableKey = key;
    const visible = rows.slice(-120).reverse();
    const fragment = document.createDocumentFragment();
    for (const row of visible) {
      const tr = document.createElement("tr");
      const values = [time(row[1]), format(row[2]), format(row[3]), format(row[4], 2), format(row[5], 2), format(row[13], 2), row[12] ? "Safe-hold" : "Armed"];
      values.forEach((value, index) => {
        const td = document.createElement("td");
        td.textContent = value;
        if (index === 6) td.className = row[12] ? "table-hold" : "table-armed";
        tr.appendChild(td);
      });
      fragment.appendChild(tr);
    }
    byId("telemetryRows").replaceChildren(fragment);
    byId("dataCount").textContent = `${visible.length} rows`;
    byId("dataEmpty").hidden = !!rows.length;
  }
  function render(state) {
    const rows = samples(), def = definitions[signal];
    const values = rows.map(row => row[def.index]).filter(Number.isFinite);
    const current = signal === "pressure" ? state.measured.pressure : signal === "level" ? state.measured.level : signal === "authority" ? state.brain.effective ?? state.brain.authority : state.valves.product;
    const target = signal === "pressure" ? state.targets?.pressure_sp : signal === "level" ? state.targets?.level_sp : signal === "valves" ? state.commanded?.product : null;
    byId("inspectorValue").textContent = format(current, def.digits);
    byId("inspectorUnit").textContent = def.unit;
    byId("inspectorMin").textContent = values.length ? format(Math.min(...values), def.digits) : "—";
    byId("inspectorMax").textContent = values.length ? format(Math.max(...values), def.digits) : "—";
    byId("inspectorAvg").textContent = values.length ? format(values.reduce((a, b) => a + b, 0) / values.length, def.digits) : "—";
    byId("inspectorN").textContent = String(values.length);
    byId("inspectorTruth").textContent = def.truth ? format(state.truth[signal], def.digits) : "Not applicable";
    byId("inspectorTarget").textContent = format(target, def.digits);
    byId("explorerSamples").textContent = `${rows.length} samples`;
    byId("explorerRange").textContent = rows.length ? `${time(rows[0][1])} – ${time(rows.at(-1)[1])}` : "No samples yet";
    const online = state.status === "running" && !state.telemetry_stale;
    connection(online, online ? "live" : state.telemetry_stale ? "stale telemetry" : state.status);
    byId("exportTelemetry").disabled = !rows.length;
    if (mode === "data") renderTable(rows);
  }
  byId("exportTelemetry").addEventListener("click", () => {
    const rows = samples();
    if (!rows.length) return;
    const csv = ["sample,time_s,ai_pressure_kpa,true_pressure_kpa,ai_level_pct,true_level_pct,feed_1_pct,feed_2_pct,purge_pct,product_pct,pressure_sp_kpa,level_sp_pct,sis_tripped,effective_authority,keel_authority,governance_authority", ...rows.map(row => row.map(v => Number.isFinite(v) ? v : "").join(","))].join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "scram-telemetry.csv"; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  window.telemetryWorkspace = {settings, render, connection};
  selectSignal("pressure");
})();
