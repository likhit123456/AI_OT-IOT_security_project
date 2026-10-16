// @ts-check
// TE Reactor operator station, wired to the sim_app backend.
//
// Reads GET /api/state every 0.5 s and sends /api/manipulate, /api/reset, /api/command,
// /api/ai/rescue and /api/ai/stop. Plain JavaScript, no build step: the JSDoc types
// below are checked by TypeScript (see sim_app/static/tsconfig.json).
"use strict";

/** @typedef {"f1" | "f2" | "purge" | "product"} Valve */
/** @typedef {Record<Valve, number | null>} ValveMap */
/** A history row: [seq, t, pressure, level, f1, f2, purge, product flows, ...valve positions]. */
/** @typedef {number[]} HistoryRow */

/**
 * @typedef {object} CommandStep
 * @property {string} [clause]
 * @property {boolean} ok
 * @property {string} [message]
 * @property {string} [error]
 * @property {string[]} [corrections]
 */

/**
 * @typedef {object} Activity
 * @property {number} id
 * @property {number} ts                 server wall-clock seconds
 * @property {string} kind               decision | command | error | info | done
 * @property {string} text
 * @property {{pressure: number, level: number}} [saw]
 * @property {{pressure?: number}} [setpoints]
 * @property {Record<string, [number, number]> | null} [valves]
 * @property {string} [command]
 * @property {CommandStep[]} [actions]
 */

/**
 * @typedef {object} PlantState
 * @property {boolean} connected
 * @property {boolean} resetting
 * @property {number} t                  seconds since the server started
 * @property {number | null} pressure
 * @property {number | null} level
 * @property {ValveMap} flows
 * @property {ValveMap} valves
 * @property {ValveMap} setpoints
 * @property {boolean} e_stop
 * @property {boolean} ruptured
 * @property {number | null} rupture_pressure
 * @property {HistoryRow[]} history
 * @property {{active: boolean, activity: Activity[]}} [ai]
 */

/** @typedef {{time: string, cls: string, tag: string, say: string, meta: string, tel: string | null}} FeedEntry */
/** @typedef {{label: string, cls: string, col: string}} Zone */

const POLL_MS = 500;
const REQUEST_TIMEOUT_MS = 4000;
const KEEP = 240;                     // chart samples: 240 at 2 Hz = last 2 min
const FEED_MAX = 40;
const P_MIN = 2200, P_MAX = 3200, LOW = 2550, DESIGN = 2700, CAUTION = 2750, DANGER = 3000, RUPT = 3100;

/** @type {[Valve, string, string][]} */
const VALVES = [
  ["f1", "Feed 1", "A + C feed · opening raises pressure & production"],
  ["f2", "Feed 2", "A-rich trim · opening raises pressure"],
  ["purge", "Purge", "vents vapour · opening lowers pressure"],
  ["product", "Product", "liquid draw-off · lowers level & pressure"],
];
/** @type {Record<Valve, string>} */
const SHORT = {f1: "feed 1", f2: "feed 2", purge: "purge", product: "product"};
/** @type {Record<Valve, string>} */
const FLOW_COLORS = {f1: "#5aa9ff", f2: "#c6a1ff", purge: "#4fd1b0", product: "#e39a5c"};
/** @type {Record<Valve, number>} */
const FLOW_COL = {f1: 4, f2: 5, purge: 6, product: 7};

// ---------- helpers

/** @param {number} v @param {number} lo @param {number} hi */
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Look up an element that the page is known to contain. */
function $(/** @type {string} */ id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el;
}
/** @param {string} id */
const button = id => /** @type {HTMLButtonElement} */ ($(id));
/** @param {string} id */
const canvas = id => /** @type {HTMLCanvasElement} */ ($(id));

/** @param {number | null | undefined} v @param {number} [digits] */
function fmt(v, digits = 1) {
  return v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toFixed(digits);
}
/** Seconds as M:SS, or H:MM:SS once past an hour. */
function fmtClock(/** @type {number} */ secs) {
  const s = Math.max(0, Math.floor(secs));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}
/** @param {unknown} s */
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c] ?? c);
/** @param {number} ts */
const timeOf = ts => new Date(ts * 1000).toLocaleTimeString([], {hour12: false});
/** @param {string} name */
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** @param {number} p @param {boolean} ruptured @returns {Zone} */
function zone(p, ruptured) {
  if (ruptured || p >= RUPT) return {label: "Rupture", cls: "z-rup", col: "--crit"};
  if (p >= DANGER) return {label: "Danger", cls: "z-crit", col: "--crit"};
  if (p >= CAUTION) return {label: "Caution", cls: "z-warn", col: "--warn"};
  if (p < LOW) return {label: "Low", cls: "z-warn", col: "--warn"};
  return {label: "Nominal", cls: "z-ok", col: "--ok"};
}

// ---------- page state

/** @type {PlantState | null} */
let plant = null;
let serverUp = false;
let lastSeq = 0, lastAct = 0;
let resettingHere = false;
/** @type {HistoryRow[]} */
const hist = [];
/** @type {FeedEntry[]} */
const feed = [];

// ---------- requests

/** @param {string} url @param {RequestInit} [init] */
async function fetchWithTimeout(url, init = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), REQUEST_TIMEOUT_MS);
  try { return await fetch(url, {...init, signal: ctl.signal}); }
  finally { clearTimeout(timer); }
}

/** POST JSON; throws an Error carrying the server's message on a non-2xx reply. */
async function post(/** @type {string} */ url, /** @type {object} */ body = {}) {
  // reset and the AI model can take a while, so only the poll uses the short timeout
  const res = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : d ? "request rejected" : res.statusText || `HTTP ${res.status}`);
  }
  return data;
}

/** @type {number | undefined} */
let toastTimer;
function toast(/** @type {string} */ msg, isError = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), isError ? 5000 : 3000);
}

/** @param {{variable: Valve | "e_stop", value?: number, delta?: number}} body */
async function move(body) {
  try {
    const r = await post("/api/manipulate", body);
    toast(r.variable === "e_stop" ? `Emergency stop ${r.value ? "on" : "released"}`
                                  : `${SHORT[/** @type {Valve} */ (r.variable)]} → ${fmt(r.setpoint)}%`);
  } catch (e) { toast(errText(e), true); }
}
/** @param {unknown} e */
const errText = e => e instanceof Error ? e.message : String(e);

// ---------- static DOM (gauge, valves, legend)

/** Gauge x position for a pressure. */
const GX = (/** @type {number} */ p) => 16 + 448 * (clamp(p, P_MIN, P_MAX) - P_MIN) / (P_MAX - P_MIN);

function buildGauge() {
  let ticks = "";
  for (let v = P_MIN; v <= P_MAX; v += 100) {
    const major = v % 200 === 0, x = GX(v).toFixed(1);
    ticks += `<line class="cr-tick ${major ? "maj" : ""}" x1="${x}" y1="74" x2="${x}" y2="${major ? 82 : 79}"/>`;
  }
  for (const v of [2200, 2600, 3000, 3200]) ticks += `<text class="cr-tlabel" x="${GX(v).toFixed(1)}" y="95" text-anchor="middle">${v}</text>`;
  /** @type {[number, number, string][]} */
  const bands = [[P_MIN, DESIGN, "ok"], [DESIGN, DANGER, "warn"], [DANGER, RUPT, "crit"], [RUPT, P_MAX, "rup"]];
  const bars = bands.map(([lo, hi, cls], i) => {
    const a = GX(lo) + (i ? 1.5 : 0), b = GX(hi);
    return `<rect class="cr-zbar-${cls}" x="${a.toFixed(1)}" y="58" width="${(b - a).toFixed(1)}" height="14" rx="2"/>`;
  }).join("");
  const marker = (/** @type {number} */ p, /** @type {string} */ label, /** @type {string} */ anchor) =>
    `<line class="cr-thr" x1="${GX(p).toFixed(1)}" y1="36" x2="${GX(p).toFixed(1)}" y2="58"/>` +
    `<text class="cr-thrlabel" x="${GX(p).toFixed(1)}" y="31" text-anchor="${anchor}">${label}</text>`;
  $("gauge").innerHTML = bars + ticks + marker(DESIGN, "DESIGN", "middle") + marker(RUPT, "RUPTURE", "end") +
    `<g class="cr-ptr" id="ptr" transform="translate(${GX(DESIGN).toFixed(1)} 0)">` +
    `<path class="cr-ptr-head" d="M-6 44 L6 44 L0 54 Z"/><line class="cr-ptr-line" x1="0" y1="50" x2="0" y2="86"/></g>`;
}

/** Per-valve elements, looked up once. */
/** @type {Record<Valve, {pos: HTMLElement, sp: HTMLElement, fill: HTMLElement, input: HTMLInputElement}>} */
const valveEls = /** @type {any} */ ({});

function buildValves() {
  $("valveList").innerHTML = VALVES.map(([k, name, sub]) => `
    <div class="valve" data-valve="${k}">
      <div class="valve-h"><span class="nm">${name}</span><span class="vv" data-vv>—</span></div>
      <div class="valve-sp" data-sp>sp —</div>
      <div class="valve-sub">${sub}</div>
      <div class="valve-track"><div class="valve-fill" data-vf style="width:0%"></div></div>
      <div class="valve-ctl">
        <button class="step" type="button" data-d="-5" title="Close 5%">-5</button>
        <button class="step" type="button" data-d="-1" title="Close 1%">-1</button>
        <button class="step" type="button" data-d="1" title="Open 1%">+1</button>
        <button class="step" type="button" data-d="5" title="Open 5%">+5</button>
        <input type="number" min="0" max="100" step="0.5" data-in placeholder="%" aria-label="${name} position (%)">
        <button class="apply" type="button" data-apply>Set</button>
      </div>
    </div>`).join("");
  for (const [k] of VALVES) {
    const row = /** @type {HTMLElement} */ (document.querySelector(`.valve[data-valve="${k}"]`));
    const q = (/** @type {string} */ sel) => /** @type {HTMLElement} */ (row.querySelector(sel));
    valveEls[k] = {pos: q("[data-vv]"), sp: q("[data-sp]"), fill: q("[data-vf]"), input: /** @type {HTMLInputElement} */ (q("[data-in]"))};
  }
}

function buildLegend() {
  $("flowLegend").innerHTML = VALVES.map(([k, name]) => `<span><i style="background:${FLOW_COLORS[k]}"></i>${name}</span>`).join("");
}

// ---------- AI feed (backend activity -> decision cards)

/** @param {Record<string, [number, number]> | null | undefined} v */
function moves(v) {
  if (!v) return "no valve change";
  const changed = Object.entries(v).filter(([, [a, b]]) => Math.abs(a - b) >= 0.1);
  return changed.length ? changed.map(([k, [a, b]]) => `${SHORT[/** @type {Valve} */ (k)] ?? k} ${fmt(a)}→${fmt(b)}%`).join(" · ")
                        : "no valve change";
}

function trend() {
  if (hist.length < 6) return "steady";
  const d = hist[hist.length - 1][2] - hist[hist.length - 6][2];
  return d > 2 ? "rising" : d < -2 ? "falling" : "steady";
}

/** The measured plant state, shown beside a decision. Only meaningful for live events. */
function truthLine(/** @type {Activity} */ a) {
  if (!plant || plant.pressure === null || Date.now() / 1000 - a.ts > 5) return null;
  return `P ${fmt(plant.pressure, 0)} kPa, ${trend()} · L ${fmt(plant.level)}% · purge ${fmt(plant.valves.purge)}% · feed 1 ${fmt(plant.valves.f1)}%`;
}

/** @param {Activity} a @returns {FeedEntry} */
function toFeed(a) {
  const time = timeOf(a.ts);
  if (a.kind === "decision") {
    const saw = a.saw ? `saw P ${fmt(a.saw.pressure, 0)} kPa, L ${fmt(a.saw.level)}% · ` : "";
    const asked = a.setpoints?.pressure !== undefined ? `asked P→${fmt(a.setpoints.pressure, 0)} · ` : "";
    return {time, cls: "", tag: "DECISION", say: esc(a.text), meta: saw + asked + esc(moves(a.valves)), tel: truthLine(a)};
  }
  if (Array.isArray(a.actions)) {
    const steps = a.actions.map(s => s.ok ? esc((s.message ?? "").replace(" setpoint -> ", " → "))
                                          : `<span class="err">${esc(s.clause || "?")}: ${esc(s.error)}</span>`);
    const fixes = a.actions.flatMap(s => s.corrections ?? []);
    return {time, cls: a.kind === "error" ? "alarm" : "", tag: "COMMAND", say: `“${esc(a.command)}”`,
            meta: steps.join(" · ") + (fixes.length ? ` · corrected: ${esc(fixes.join("; "))}` : ""), tel: null};
  }
  if (a.kind === "error") return {time, cls: "alarm", tag: "ALARM", say: esc(a.text), meta: "", tel: null};
  if (a.kind === "done") return {time, cls: "engage", tag: "STABILISED", say: esc(a.text), meta: "", tel: truthLine(a)};
  const engage = a.text.startsWith("AI engaged");
  return {time, cls: engage ? "engage" : "", tag: engage ? "ENGAGE" : "INFO", say: esc(a.text), meta: "", tel: null};
}

function renderFeed() {
  const body = $("aiBody");
  if (!feed.length) {
    body.innerHTML = `<div class="ai-idle">Type a plain-English command (several at once is fine), or press <b>Save the reactor</b> to hand control to the trained model. Its decisions appear here with the measured readings beside each one.</div>`;
    return;
  }
  const old = body.querySelector(".ai-list");
  const scrolled = old ? old.scrollTop : 0;          // keep the reader's place when new entries arrive
  body.innerHTML = `<div class="ai-list">` + feed.map(d => `<div class="dec ${d.cls}">
    <div class="dec-h"><span class="dec-t">${d.time}</span><span class="dec-tag">${d.tag}</span></div>
    <div class="dec-say">${d.say}</div>${d.meta ? `<div class="dec-meta">${d.meta}</div>` : ""}${d.tel ? `<div class="dec-tel">${esc(d.tel)}</div>` : ""}</div>`).join("") + `</div>`;
  if (scrolled) /** @type {HTMLElement} */ (body.querySelector(".ai-list")).scrollTop = scrolled;
}

// ---------- render off the backend state

function render() {
  const s = plant;
  const station = $("station");
  if (!s) { $("chip").textContent = serverUp ? "CONNECTING" : "SERVER DOWN"; return; }
  const ai = !!s.ai?.active;
  const p = s.pressure;
  const z = zone(p ?? DESIGN, s.ruptured);
  const resetting = s.resetting || resettingHere;
  const offline = !serverUp || !s.connected;

  station.classList.toggle("ai-on", ai);
  station.classList.toggle("ruptured", s.ruptured);
  station.classList.toggle("estopped", s.e_stop && !ai && !s.ruptured);
  $("clock").textContent = fmtClock(s.t);
  $("clock").title = "time since the server started";
  $("chip").textContent = !serverUp ? "SERVER DOWN" : resetting ? "RESETTING" : !s.connected ? "NO PLANT"
    : s.ruptured ? "RUPTURE" : ai ? "AI IN CONTROL" : s.e_stop ? "E-STOP" : "MANUAL";

  // pressure instrument
  $("pVal").textContent = fmt(p, 0);
  const zoneEl = $("zone");
  zoneEl.textContent = p === null ? "No data" : z.label;
  zoneEl.className = "cr-zone " + (p === null ? "" : z.cls);
  const led = $("led"), col = cssVar(p === null ? "--dim" : z.col);
  led.style.background = col;
  led.style.boxShadow = "0 0 10px " + col;
  $("ptr").setAttribute("transform", `translate(${GX(p ?? DESIGN).toFixed(1)} 0)`);
  $("lVal").textContent = fmt(s.level) + "%";
  $("fVal").textContent = fmt(s.flows.product);
  $("hudP").textContent = fmt(p, 0) + " kPa";
  $("hudP").style.color = col;
  $("hudL").textContent = "Level " + fmt(s.level) + "%";
  $("viewNote").textContent = s.ruptured ? `ruptured at ${fmt(s.rupture_pressure, 0)} kPa · reset required` : "live · GRFICS 3D";

  // valves
  for (const [k] of VALVES) {
    const el = valveEls[k], pos = s.valves[k];
    el.pos.textContent = fmt(pos) + "%";
    el.sp.textContent = "sp " + fmt(s.setpoints[k] ?? pos) + "%";
    el.fill.style.width = clamp(pos ?? 0, 0, 100) + "%";
  }
  const locked = s.ruptured || resetting || offline || ai;
  document.querySelectorAll("#valveList button, #valveList input").forEach(el => {
    /** @type {HTMLButtonElement | HTMLInputElement} */ (el).disabled = locked || s.e_stop;
  });
  const es = button("btnEstop");
  es.disabled = locked;
  es.classList.toggle("on", s.e_stop);
  es.textContent = s.e_stop ? "RELEASE EMERGENCY STOP" : "EMERGENCY STOP";
  const lockMsg = s.ruptured ? "Reactor ruptured. Reset the plant to continue."
    : resetting ? "Resetting. Controls are locked."
    : offline ? "Waiting for the plant."
    : ai ? "AI in control. Manual valves are locked."
    : s.e_stop ? "Emergency stop is on. Release it to move valves." : "";
  $("lockText").textContent = lockMsg;
  $("lockrow").classList.toggle("show", !!lockMsg);

  // AI and reset
  $("aiState").textContent = s.ruptured ? "halted" : ai ? "in control" : "idle";
  button("btnSave").disabled = locked;
  button("btnStop").disabled = !ai || !serverUp;
  button("btnSend").disabled = !serverUp;
  button("btnReset").disabled = resetting || !serverUp;
  $("busy").hidden = !resetting;

  drawGraphs();
}

// ---------- charts

/** Size a canvas to its box at device resolution; returns the context and CSS size. */
function setup(/** @type {HTMLCanvasElement} */ c) {
  const dpr = Math.min(2, window.devicePixelRatio || 1), r = c.getBoundingClientRect();
  const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  const x = /** @type {CanvasRenderingContext2D} */ (c.getContext("2d"));
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, r.width, r.height);
  return /** @type {const} */ ([x, r.width, r.height]);
}

/**
 * @param {CanvasRenderingContext2D} x @param {number[]} data
 * @param {(i: number) => number} X @param {(v: number) => number} Y
 * @param {string} color @param {number} [fillTo]  y to shade down to
 */
function drawSeries(x, data, X, Y, color, fillTo) {
  if (data.length < 2) return;
  const path = () => { x.beginPath(); data.forEach((v, i) => i ? x.lineTo(X(i), Y(v)) : x.moveTo(X(i), Y(v))); };
  if (fillTo !== undefined) {
    const g = x.createLinearGradient(0, 0, 0, fillTo);
    g.addColorStop(0, color + "33"); g.addColorStop(1, color + "00");
    path(); x.lineTo(X(data.length - 1), fillTo); x.lineTo(X(0), fillTo); x.closePath();
    x.fillStyle = g; x.fill();
  }
  path(); x.strokeStyle = color; x.lineWidth = 2; x.stroke();
}

function drawGraphs() {
  const rows = hist.slice(-KEEP);
  const n = Math.max(2, rows.length);
  const P = rows.map(r => r[2]), L = rows.map(r => r[3]);
  const accent = cssVar("--accent"), dim = cssVar("--dim"), line = cssVar("--line"), crit = cssVar("--crit");
  const mono = "10px " + cssVar("--font-mono");

  // pressure, fixed 2200-3200 kPa so the bands are always in the same place
  {
    const [x, w, h] = setup(canvas("gPressure"));
    const pad = {l: 38, r: 8, t: 8, b: 16}, lo = P_MIN, hi = P_MAX;
    const X = (/** @type {number} */ i) => pad.l + (w - pad.l - pad.r) * i / (n - 1);
    const Y = (/** @type {number} */ v) => pad.t + (h - pad.t - pad.b) * (1 - (clamp(v, lo, hi) - lo) / (hi - lo));
    x.font = mono;
    for (const v of [2400, 2700, 3000]) {
      x.strokeStyle = line; x.globalAlpha = .5; x.beginPath(); x.moveTo(pad.l, Y(v)); x.lineTo(w - pad.r, Y(v)); x.stroke();
      x.globalAlpha = 1; x.fillStyle = dim; x.fillText(String(v), 4, Y(v) + 3);
    }
    x.strokeStyle = crit; x.setLineDash([5, 4]); x.beginPath(); x.moveTo(pad.l, Y(RUPT)); x.lineTo(w - pad.r, Y(RUPT)); x.stroke(); x.setLineDash([]);
    x.fillStyle = crit; x.textAlign = "right"; x.fillText("3100 rupture", w - pad.r, Y(RUPT) - 4); x.textAlign = "left";
    drawSeries(x, P, X, Y, accent, h - pad.b);
    if (P.length) {
      const last = P[P.length - 1];
      x.fillStyle = cssVar(zone(last, !!plant?.ruptured).col);
      x.beginPath(); x.arc(X(P.length - 1), Y(last), 3.5, 0, 2 * Math.PI); x.fill();
    }
  }
  // level, 20-80 % unless the data leaves that band
  {
    const [x, w, h] = setup(canvas("gLevel"));
    const lo = Math.min(20, ...L), hi = Math.max(80, ...L);
    const X = (/** @type {number} */ i) => 30 + (w - 34) * i / (n - 1);
    const Y = (/** @type {number} */ v) => 6 + (h - 18) * (1 - (v - lo) / (hi - lo));
    x.font = mono; x.fillStyle = dim;
    for (const v of [lo, (lo + hi) / 2, hi]) x.fillText(String(Math.round(v)), 2, Y(v) + 3);
    drawSeries(x, L, X, Y, accent, h - 12);
  }
  // flows, four series on one scale
  {
    const [x, w, h] = setup(canvas("gFlows"));
    let fmax = 1;
    for (const r of rows) for (const k of VALVES) fmax = Math.max(fmax, r[FLOW_COL[k[0]]] || 0);
    const X = (/** @type {number} */ i) => 30 + (w - 34) * i / (n - 1);
    const Y = (/** @type {number} */ v) => 6 + (h - 14) * (1 - v / (fmax * 1.1));
    x.font = mono; x.fillStyle = dim;
    x.fillText(String(Math.round(fmax)), 2, Y(fmax) + 3); x.fillText("0", 2, Y(0));
    for (const [k] of VALVES) drawSeries(x, rows.map(r => r[FLOW_COL[k]] || 0), X, Y, FLOW_COLORS[k]);
  }
}

// ---------- 3D display: GRFICS rendered at desktop size, scaled to fill the screen,
// with its side menu and header cropped off

function fitView() {
  const vp = $("screen"), f = $("plant3d");
  const vw = 1400 - 270, vh = 900 - 40;
  const k = 1.03 * Math.max(vp.clientWidth / vw, vp.clientHeight / vh);   // 3% extra hides Unity's letterbox edges
  const dx = (vp.clientWidth - vw * k) / 2 - 270 * k, dy = (vp.clientHeight - vh * k) / 2 - 40 * k;
  f.style.transform = `translate(${dx}px, ${dy}px) scale(${k})`;
}

// ---------- events

function wireEvents() {
  new ResizeObserver(fitView).observe($("screen"));
  button("btnFull").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else $("screen").requestFullscreen().catch(() => toast("Full screen is not allowed here", true));
  });
  document.addEventListener("fullscreenchange", () => {
    button("btnFull").textContent = document.fullscreenElement ? "Exit full screen" : "Full screen";
    fitView();
  });

  $("valveList").addEventListener("click", e => {
    const t = /** @type {HTMLElement} */ (e.target);
    const row = /** @type {HTMLElement | null} */ (t.closest(".valve"));
    if (!row) return;
    const k = /** @type {Valve} */ (row.dataset.valve);
    if (t.matches(".step")) move({variable: k, delta: Number(t.dataset.d)});
    if (t.matches("[data-apply]")) {
      const input = valveEls[k].input, v = parseFloat(input.value);
      if (!Number.isFinite(v)) { toast("Enter a value from 0 to 100", true); return; }
      input.value = "";
      move({variable: k, value: clamp(v, 0, 100)});
    }
  });
  $("valveList").addEventListener("keydown", e => {
    const t = /** @type {HTMLElement} */ (e.target);
    if (e.key === "Enter" && t.matches("[data-in]")) /** @type {HTMLElement} */ (t.closest(".valve")?.querySelector("[data-apply]")).click();
  });
  button("btnEstop").addEventListener("click", () => move({variable: "e_stop", value: plant?.e_stop ? 0 : 1}));

  button("btnReset").addEventListener("click", async () => {
    resettingHere = true; render();
    try {
      const r = await post("/api/reset");
      hist.length = 0;
      toast(`Plant reset: ${fmt(r.pressure, 0)} kPa`);
      /** @type {HTMLIFrameElement} */ ($("plant3d")).src = "http://localhost/";   // the rupture latch lives in the 3D client
    } catch (e) { toast(errText(e), true); }
    resettingHere = false; render();
  });
  button("btnSave").addEventListener("click", async () => {
    try { await post("/api/ai/rescue"); toast("AI is taking control"); } catch (e) { toast(errText(e), true); }
  });
  button("btnStop").addEventListener("click", async () => {
    try { await post("/api/ai/stop"); toast("AI stopped. Manual control."); } catch (e) { toast(errText(e), true); }
  });

  $("cmdForm").addEventListener("submit", e => {
    e.preventDefault();
    const input = /** @type {HTMLInputElement} */ ($("cmd"));
    const text = input.value.trim();
    if (!text) return;
    queue.push(text);
    input.value = "";
    sendLabel();
    runQueue();
  });
  window.addEventListener("resize", render);
}

// Commands queue, so the next one can be sent while the AI is still reading the last.
/** @type {string[]} */
const queue = [];
let busy = false;
function sendLabel() {
  button("btnSend").textContent = !busy ? "Send" : queue.length ? `Working (${queue.length} waiting)` : "Working…";
}
async function runQueue() {
  if (busy) return;
  busy = true;
  while (queue.length) {
    const text = /** @type {string} */ (queue.shift());
    sendLabel();
    try { toast((await post("/api/command", {text})).message); }
    catch (e) { toast(`“${text}”: ${errText(e)}`, true); }
  }
  busy = false;
  sendLabel();
}

// ---------- poll

async function poll() {
  let again = POLL_MS;
  try {
    const res = await fetchWithTimeout(`/api/state?since=${lastSeq}&since_ai=${lastAct}`, {cache: "no-store"});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    /** @type {PlantState} */
    const s = await res.json();
    if (plant && s.t < plant.t) {
      // the server restarted: its sequence numbers and activity ids start again from 1,
      // so start over and fetch everything it has
      lastSeq = lastAct = 0;
      hist.length = feed.length = 0;
      renderFeed();
      plant = null;
      again = 0;
      return;                                   // the finally block polls again straight away
    }
    for (const row of s.history ?? []) { hist.push(row); lastSeq = row[0]; }
    if (hist.length > KEEP * 2) hist.splice(0, hist.length - KEEP);
    const acts = s.ai?.activity ?? [];
    plant = s;
    serverUp = true;
    for (const a of acts) { lastAct = a.id; feed.unshift(toFeed(a)); }
    if (feed.length > FEED_MAX) feed.length = FEED_MAX;
    if (acts.length) renderFeed();
  } catch {
    serverUp = false;
  } finally {
    render();
    setTimeout(poll, again);
  }
}

buildGauge();
buildValves();
buildLegend();
renderFeed();
wireEvents();
fitView();
poll();
