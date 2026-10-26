
// @ts-check
const $ = id => /** @type {HTMLElement} */ (document.getElementById(id));
const inp = id => /** @type {HTMLInputElement} */ (document.getElementById(id));
/** trend rows: [seq,t,Pm,Pt,Lm,Lt,f1,f2,purge,prod,spP,spL,tripped,authEff,keelA,govApplied] */
const H = [];
const EV = [];
const SUS = [];          // [t, observer suspicion] for the KPI sparkline
const REJ = [];          // [t, gate rejections so far] for the KPI sparkline
let lastH = 0, lastE = 0, WIN = 600, FILTER = "all", S = null, lastDec = null, lastChat = -1, TAB = "overview";
let OVERVIEW_SIGNAL = "pressure";
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fx = (v, d = 1) => v === null || v === undefined || Number.isNaN(+v) ? "–" : Number(v).toFixed(d);
const GROUP = {attack: ["attack"], defense: ["gate", "keel", "governance", "sis", "monitor"], observer: ["observer"],
               plant: ["plant", "brain", "error"]};
const PROPOSAL_ATTACK = ["provenance_violation", "rationale_mismatch", "setpoint_drift"];
const RING_C = 2 * Math.PI * 58;
const VALVES = ["f1", "f2", "purge", "product"];
const FLOW = {f1: "f1_flow", f2: "f2_flow", purge: "purge_flow", product: "product_flow"};
const VNAME = {f1: "Feed 1", f2: "Feed 2", purge: "Purge", product: "Product"};
const SPNAME = {pressure_sp: "P", level_sp: "L", production_rate_sp: "F", a_comp_sp: "A"};
const SVGNS = "http://www.w3.org/2000/svg";
const stateColor = v => v <= 0.15 ? css("--alert") : v < 0.999 ? css("--warn") : css("--ok");

async function act(action, extra = {}) {
  if (S && (S.telemetry_stale || S.status !== "running")) {
    showBanner("Waiting for current plant telemetry before accepting controls."); return false;
  }
  try {
    const r = await fetch("/api/action", {method: "POST", headers: {"Content-Type": "application/json"},
                                         body: JSON.stringify({action, ...extra})});
    const j = await r.json();
    showBanner(j.ok ? "" : j.error);
    return !!j.ok;
  } catch (e) { showBanner("Control room server not reachable: " + e.message); return false; }
}
function showBanner(msg) { const b = $("banner"); b.textContent = msg; b.hidden = !msg; }
document.querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", () => act(/** @type {HTMLElement} */ (b).dataset.act)));
document.querySelectorAll("[data-brain]").forEach(b => b.addEventListener("click", () => act("brain", {kind: /** @type {HTMLElement} */ (b).dataset.brain})));
document.querySelectorAll("[data-win]").forEach(b => b.addEventListener("click", () => {
  WIN = +(/** @type {HTMLElement} */ (b).dataset.win);
  document.querySelectorAll("[data-win]").forEach(x => {
    const selected = +x.dataset.win === WIN;
    x.classList.toggle("on", selected); x.setAttribute("aria-pressed", String(selected));
  });
  overviewTooltip.hidden = true;
  draw();
}));
document.querySelectorAll("[data-series]").forEach(b => b.addEventListener("click", () => {
  OVERVIEW_SIGNAL = b.dataset.series;
  document.querySelectorAll("[data-series]").forEach(x => {
    const selected = x === b;
    x.classList.toggle("on", selected); x.setAttribute("aria-pressed", String(selected));
  });
  overviewTooltip.hidden = true;
  if (S) renderSummary(S);
  draw();
}));
document.querySelectorAll("[data-f]").forEach(b => b.addEventListener("click", () => {
  FILTER = /** @type {HTMLElement} */ (b).dataset.f || "all";
  document.querySelectorAll("[data-f]").forEach(x => x.classList.toggle("on", x === b)); renderLog();
}));
let authTimer = 0;
inp("auth").addEventListener("input", e => {
  const v = +(/** @type {HTMLInputElement} */ (e.target).value);
  $("authv").textContent = v.toFixed(2);
  clearTimeout(authTimer); authTimer = setTimeout(() => act("authority", {value: v}), 150);
});

// ---------- tabs (remembered per viewer) ----------
function setTab(name) {
  TAB = name;
  const titles = {overview: ["Plant overview", "Your process at a glance. Every signal, every safeguard."], console: ["Operator console", "Direct the process. Explore scenarios. See how the defenses respond."], analysis: ["Telemetry & logs", "Follow the signals, inspect decisions, and trace every intervention."]};
  $("pageTitle").textContent = titles[name][0];
  $("pageDescription").textContent = titles[name][1];
  document.querySelectorAll("[data-tab]").forEach(b => b.classList.toggle("on", /** @type {HTMLElement} */ (b).dataset.tab === name));
  document.querySelectorAll("[data-tab]").forEach(b => {
    const selected = b.dataset.tab === name;
    b.setAttribute("aria-selected", String(selected));
    b.setAttribute("tabindex", selected ? "0" : "-1");
    b.id = "tab-" + b.dataset.tab;
    b.setAttribute("aria-controls", "view-" + b.dataset.tab);
  });
  document.querySelectorAll("[data-view]").forEach(v => { /** @type {HTMLElement} */ (v).hidden = /** @type {HTMLElement} */ (v).dataset.view !== name; });
  document.querySelectorAll("[data-view]").forEach(v => { v.id = "view-" + v.dataset.view; v.setAttribute("role", "tabpanel"); v.setAttribute("aria-labelledby", "tab-" + v.dataset.view); });
  try { localStorage.setItem("scram.tab", name); } catch (e) { /* storage unavailable */ }
  if (name === "analysis") { renderLog(); draw(); }
  if (name === "console") { lastChat = -1; if (S) renderChat(S); }
  if (S && S.brain) render(S);
}
document.querySelector(".tabs").addEventListener("keydown", e => {
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return;
  e.preventDefault();
  const tabs = [...document.querySelectorAll("[data-tab]")];
  const index = tabs.indexOf(document.activeElement);
  const next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (index + (["ArrowRight", "ArrowDown"].includes(e.key) ? 1 : -1) + tabs.length) % tabs.length;
  setTab(tabs[next].dataset.tab); tabs[next].focus();
});
document.querySelectorAll("[data-tab]").forEach(b => b.addEventListener("click", () => setTab(/** @type {HTMLElement} */ (b).dataset.tab || "overview")));

// ---------- chat ----------
const say = /** @type {HTMLTextAreaElement} */ (document.getElementById("say"));
async function send(text) {
  text = text.trim();
  if (!text) return;
  if (await act("chat", {text})) say.value = "";
}
$("compose").addEventListener("submit", e => { e.preventDefault(); send(say.value); });
say.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(say.value); } });
document.querySelectorAll("[data-say]").forEach(b => b.addEventListener("click", () => send(/** @type {HTMLElement} */ (b).dataset.say || "")));

function spChips(sp) {
  const [p, l, f, a] = sp || [];
  if ([p, l, f, a].every(v => v === null || v === undefined)) return `<span>hold</span>`;
  return [["P", p, 0, "kPa"], ["L", l, 1, "%"], ["F", f, 0, "kmol/h"], ["A", a, 1, "%"]]
    .filter(x => x[1] !== null && x[1] !== undefined).map(x => `<span>${x[0]} ${fx(x[1], +x[2])} ${x[3]}</span>`).join("");
}
function renderChat(s) {
  const chat = s.chat || [], last = chat.length ? chat[chat.length - 1].id : 0;
  const waiting = !!s.note && chat.length > 0 && chat[chat.length - 1].role === "you";
  $("notebar").hidden = !s.note; $("noteTxt").textContent = s.note || "";
  const man = Object.entries(s.manual || {});
  $("manualbar").hidden = !man.length;
  $("manualTxt").textContent = man.map(([v, p]) => `${VNAME[v] || v} ${fx(p, 0)}% (now ${fx(s.valves[v], 0)}%)`).join(" · ");
  $("chatBrain").textContent = s.brain.kind === "ollama" ? "scram-operator · decides every 5 s" : "baseline rules (no chat)";
  const key = last * 2 + (waiting ? 1 : 0);
  // the manual bar's live valve reading updates every poll; the message list only on change
  if (key === lastChat) return;
  lastChat = key;
  const box = $("msgs");
  box.innerHTML = "";
  if (!chat.length) {
    box.innerHTML = `<div class="emptychat"><b>Run the plant from here</b>Setpoint orders (pressure, level, production) go to the AI operator, whose next decision is its reply. Valve orders ("feed 1 to 40%") put that valve in manual. Either way the gate, Keel, the observers and the SIS still stand between you and the reactor.</div>`;
  }
  chat.forEach(m => {
    const d = document.createElement("div");
    const who = {you: "You", brain: "AI operator", defense: "Defenses", operator: "Operator override", system: ""}[m.role] || m.role;
    d.className = "msg " + m.role + (m.role === "defense" ? (m.eff < 0.5 ? " cut" : m.eff < 0.999 ? " lim" : "") : "");
    if (who) { const w = document.createElement("span"); w.className = "who"; w.textContent = `${who} · ${fx(m.t, 0)} s`; d.appendChild(w); }
    d.appendChild(document.createTextNode(m.text || (m.role === "brain" ? "(no rationale)" : "")));
    if (m.role === "you" && m.asked) {
      const a = document.createElement("span"); a.className = "asked";
      a.textContent = "→ to the AI as targets: " + Object.entries(m.asked).map(([k, v]) => `${SPNAME[k] || k} ${v}`).join(", ");
      d.appendChild(a);
    } else if (m.role === "you" && m.note_id) {
      const a = document.createElement("span"); a.className = "asked"; a.textContent = "→ to the AI verbatim"; d.appendChild(a);
    }
    if (m.role === "brain") {
      const c = document.createElement("div"); c.className = "sps"; c.innerHTML = spChips(m.sp); d.appendChild(c);
      if (m.outcome && m.outcome !== "ok" && !(m.text || "").includes(m.outcome)) { const o = document.createElement("div"); o.className = "who"; o.style.marginTop = "6px"; o.textContent = m.outcome; d.appendChild(o); }
    }
    box.appendChild(d);
  });
  if (waiting) { const t = document.createElement("div"); t.className = "typing"; t.textContent = "AI operator is deciding…"; box.appendChild(t); }
  box.scrollTop = box.scrollHeight;
}

// ---------- attack builder ----------
let ATK = "sensor";
document.querySelectorAll("[data-atk]").forEach(b => b.addEventListener("click", () => {
  ATK = /** @type {HTMLElement} */ (b).dataset.atk || "sensor";
  document.querySelectorAll("[data-atk]").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll("[data-form]").forEach(f => { /** @type {HTMLElement} */ (f).hidden = /** @type {HTMLElement} */ (f).dataset.form !== ATK; });
}));
const SEV = {drift: ["Drift rate (units per hour)", "60"], noise: ["Noise amplitude", "20"], dropout: ["Dropout duty (0 to 1)", "0.5"], frozen: ["Severity (unused)", "0"]};
inp("sfMode").addEventListener("change", () => {
  const m = inp("sfMode").value, [label, dflt] = SEV[m];
  $("sfSevL").textContent = label; inp("sfSev").value = dflt; inp("sfSev").disabled = m === "frozen";
});
[["skF1", "skF1v"], ["skF2", "skF2v"], ["skPg", "skPgv"], ["skPr", "skPrv"], ["obPg", "obPgv"], ["obPr", "obPrv"]].forEach(([i, o]) => {
  inp(i).addEventListener("input", () => { $(o).textContent = inp(i).value + "%"; });
});
$("goSensor").addEventListener("click", () => act("sensor_fault", {channel: inp("sfCh").value, mode: inp("sfMode").value, severity: +inp("sfSev").value || 0}));
$("goValve").addEventListener("click", () => act("actuator_fault", {valve: inp("vfV").value, how: inp("vfHow").value}));
$("goStolen").addEventListener("click", () => act("stolen_key", {f1: +inp("skF1").value, f2: +inp("skF2").value, purge: +inp("skPg").value,
                                                                  product: +inp("skPr").value, duration_s: +inp("skDur").value}));
$("goForged").addEventListener("click", () => act("forged", {duration_s: +inp("fgDur").value}));
$("goOob").addEventListener("click", () => act("oob_write", {purge: +inp("obPg").value, product: +inp("obPr").value}));

// ---------- instruments: pressure dial + tank (built once, then only moved) ----------
/** @param {string} tag @param {Record<string, string|number>} attrs @param {Element=} parent */
function el(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, String(v)));
  if (parent) parent.appendChild(e);
  return e;
}
const DIAL = {min: 2000, max: 3200, cx: 150, cy: 150, r: 112, a0: 135, sweep: 270};
const dAng = v => DIAL.a0 + (Math.max(DIAL.min, Math.min(DIAL.max, v)) - DIAL.min) / (DIAL.max - DIAL.min) * DIAL.sweep;
const pt = (r, deg) => [DIAL.cx + r * Math.cos(deg * Math.PI / 180), DIAL.cy + r * Math.sin(deg * Math.PI / 180)];
function arc(r, a1, a2) {
  const [x1, y1] = pt(r, a1), [x2, y2] = pt(r, a2);
  return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 ${a2 - a1 > 180 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}
function buildDial(trip, rupture) {
  const d = $("dial");
  el("path", {d: arc(DIAL.r, DIAL.a0, DIAL.a0 + DIAL.sweep), class: "trk", fill: "none", "stroke-width": 16, "stroke-linecap": "round"}, d);
  const zones = [[DIAL.min, trip - 150, "z-ok"], [trip - 150, trip, "z-warn"], [trip, rupture, "z-trip"], [rupture, DIAL.max, "z-rup"]];
  zones.forEach(([a, b, c]) => el("path", {d: arc(DIAL.r, dAng(+a) + .6, dAng(+b) - .6), class: String(c), fill: "none", "stroke-width": 16}, d));
  for (let v = DIAL.min; v <= DIAL.max; v += 50) {
    const maj = v % 200 === 0, a = dAng(v), [x1, y1] = pt(DIAL.r - 14, a), [x2, y2] = pt(DIAL.r - (maj ? 26 : 20), a);
    el("line", {x1, y1, x2, y2, class: "tk" + (maj ? " maj" : "")}, d);
    if (maj && v % 400 === 0 && v > DIAL.min && v < DIAL.max) { const [tx, ty] = pt(DIAL.r - 40, a); const t = el("text", {x: tx, y: ty + 3.5, "text-anchor": "middle", class: "tl"}, d); t.textContent = String(v); }
  }
  [[trip, "TRIP"], [rupture, "RUPTURE"]].forEach(([v, lab]) => {
    const [tx, ty] = pt(DIAL.r + 16, dAng(+v)); const t = el("text", {x: tx, y: ty + 3, "text-anchor": +v > 2600 ? "start" : "end", class: "thr"}, d); t.textContent = String(lab);
  });
  const m = el("g", {class: "mneedle", id: "mNeedle"}, d);
  el("path", {d: `M${DIAL.cx + DIAL.r + 11} ${DIAL.cy} l9 -6 v12 z`, fill: css("--meas")}, m);
  const n = el("g", {class: "needle", id: "needle"}, d);
  el("path", {d: `M${DIAL.cx - 14} ${DIAL.cy - 3.5} L${DIAL.cx + DIAL.r - 22} ${DIAL.cy - 1} L${DIAL.cx + DIAL.r - 22} ${DIAL.cy + 1} L${DIAL.cx - 14} ${DIAL.cy + 3.5} Z`,
              fill: css("--ink"), style: "filter:drop-shadow(0 0 6px rgba(255,236,214,.5))"}, n);
  el("circle", {cx: DIAL.cx, cy: DIAL.cy, r: 11, class: "hub"}, d);
  el("circle", {cx: DIAL.cx, cy: DIAL.cy, r: 4, fill: css("--accent")}, d);
  const v = el("text", {x: DIAL.cx, y: DIAL.cy + 62, "text-anchor": "middle", class: "val", id: "dialVal"}, d); v.textContent = "–";
  const u = el("text", {x: DIAL.cx, y: DIAL.cy + 80, "text-anchor": "middle", class: "unit"}, d); u.textContent = "KPA · TRUE";
}
const TANK = {x: 18, y: 14, w: 64, h: 192};
function buildTank(lo, hi) {
  const t = $("tank");
  const defs = el("defs", {}, t), g = el("linearGradient", {id: "liq", x1: 0, y1: 0, x2: 0, y2: 1}, defs);
  el("stop", {offset: 0, "stop-color": css("--accent2"), "stop-opacity": .65}, g); el("stop", {offset: 1, "stop-color": css("--accent"), "stop-opacity": .85}, g);
  el("clipPath", {id: "tankclip"}, defs).appendChild(el("rect", {x: TANK.x, y: TANK.y, width: TANK.w, height: TANK.h, rx: 14}));
  el("rect", {x: TANK.x, y: TANK.y, width: TANK.w, height: TANK.h, rx: 14, class: "shell"}, t);
  el("rect", {x: TANK.x, y: TANK.y + TANK.h, width: TANK.w, height: 0, fill: "url(#liq)", class: "liquid", id: "liquid", "clip-path": "url(#tankclip)"}, t);
  const Y = v => TANK.y + TANK.h * (1 - v / 100);
  [0, 25, 50, 75, 100].forEach(v => { const tx = el("text", {x: TANK.x + TANK.w + 6, y: Y(v) + 3, class: "grad"}, t); tx.textContent = String(v); });
  [lo, hi].forEach(v => el("line", {x1: TANK.x - 6, x2: TANK.x + TANK.w + 2, y1: Y(v), y2: Y(v), class: "tline"}, t));
  const tl = el("text", {x: TANK.x - 8, y: Y(lo) + 3, "text-anchor": "end", class: "tlab"}, t); tl.textContent = `${lo}`;
  const th = el("text", {x: TANK.x - 8, y: Y(hi) + 3, "text-anchor": "end", class: "tlab"}, t); th.textContent = `${hi}`;
  el("line", {x1: TANK.x - 4, x2: TANK.x + TANK.w + 4, y1: TANK.y + TANK.h, y2: TANK.y + TANK.h, class: "mline", id: "mLine"}, t);
}
let built = false;
function renderInstruments(s) {
  const lim = s.sis.limits;
  if (!built) { buildDial(lim.pressure_high, 3100); buildTank(lim.level_low, lim.level_high); built = true; }
  const ptrue = s.truth.pressure, pm = s.measured.pressure;
  $("needle").style.transform = `rotate(${dAng(ptrue ?? DIAL.min)}deg)`;
  $("mNeedle").style.transform = `rotate(${dAng(pm ?? DIAL.min)}deg)`;
  $("dialVal").textContent = fx(ptrue, 0);
  const zone = $("pZone"), z = ptrue >= 3100 ? ["alert", "Rupture"] : ptrue >= lim.pressure_high ? ["alert", "Trip"] :
    ptrue >= lim.pressure_high - 150 ? ["warn", "Caution"] : ["ok", "Nominal"];
  zone.className = "zone " + (s.sis.tripped ? "warn" : z[0]); zone.textContent = s.sis.tripped ? "Safe-hold" : z[1];
  $("pMeas").innerHTML = `${fx(pm, 0)}<small>kPa</small>`;
  $("pMeas").style.color = Math.abs(pm - ptrue) >= 1 ? css("--meas") : "";
  $("pMargin").innerHTML = `${fx(lim.pressure_high - ptrue, 0)}<small>kPa</small>`;
  $("pMargin").style.color = lim.pressure_high - ptrue < 150 ? css("--alert") : "";
  const lt = s.truth.level, lm = s.measured.level, Y = v => TANK.y + TANK.h * (1 - Math.max(0, Math.min(100, v)) / 100);
  const liq = $("liquid"); liq.setAttribute("y", String(Y(lt))); liq.setAttribute("height", String(TANK.y + TANK.h - Y(lt)));
  $("mLine").style.transform = `translateY(${Y(lm) - (TANK.y + TANK.h)}px)`;
  $("lTrue").innerHTML = `${fx(lt, 2)}<small>%</small>`;
  $("lMeas").innerHTML = `${fx(lm, 2)}<small>%</small>`;
  $("lMeas").style.color = Math.abs(lm - lt) >= 0.5 ? css("--meas") : "";
  $("lTrips").textContent = `trips ${lim.level_low}% / ${lim.level_high}%`;
}

function renderDashboard(s) {
  const cmd = s.commanded || {};
  VALVES.forEach(v => {
    const pos = s.valves[v], c = cmd[v], flow = s.measured[FLOW[v]];
    const hasC = c !== undefined && c !== null;
    $(`vp-${v}`).innerHTML = `${fx(pos, 1)}<small>%</small>`;
    $(`vf-${v}`).style.width = `${Math.max(0, Math.min(100, pos ?? 0))}%`;
    const k = $(`vk-${v}`); k.style.left = `${Math.max(0, Math.min(100, hasC ? c : pos ?? 0))}%`; k.style.opacity = hasC ? "1" : "0";
    $(`vm-${v}`).textContent = hasC ? `cmd ${fx(c, 1)}%` : "cmd –";
    $(`vq-${v}`).textContent = `${fx(flow, 1)} kmol/h`;
    const man = s.manual && s.manual[v] !== undefined;
    const card = $(`vc-${v}`);
    card.classList.toggle("manual", man);
    card.classList.toggle("warn", !man && hasC && pos !== null && Math.abs(c - pos) > 1.0 && !s.sis.tripped);
    /** @type {HTMLElement} */ (card.querySelector(".flag")).textContent = man ? `manual ${fx(s.manual[v], 0)}%` : "not following";
  });
}

function renderRecovery(s) {
  const sis = s.sis, box = $("recov");
  box.hidden = !sis.tripped;
  const pill = $("sisPill");
  const hold = sis.tripped && !sis.escalated && sis.cause !== "sis_sensor_fault";
  pill.className = "pill " + (!sis.tripped ? "armed" : hold ? "hold" : "tripped");
  pill.textContent = !sis.tripped ? "SIS ARMED" : hold ? "SIS SAFE-HOLD" : "SIS TRIPPED";
  /** @type {HTMLButtonElement} */ ($("btnReset")).disabled = !sis.tripped;
  /** @type {HTMLButtonElement} */ ($("btnReset2")).disabled = !sis.can_reset;
  $("btnReset").classList.toggle("hot", !!sis.can_reset);
  if (!sis.tripped) return;
  box.classList.toggle("alert", !hold);
  $("recTitle").textContent = `SIS tripped: ${sis.reason}`;
  $("recSub").textContent = (sis.safe_state || "") + " · the AI is not consulted";
  const tr = s.truth, lim = sis.limits;
  let frac, eta;
  if (sis.lockout) { frac = 1; eta = "auto re-arm locked out · re-arm manually"; }
  else if (sis.auto_rearm_in !== null && sis.auto_rearm_in !== undefined) { frac = 1 - sis.auto_rearm_in / 10; eta = `re-arming in ${fx(sis.auto_rearm_in, 0)} s`; }
  else {
    // progress toward the re-arm band: pressure from the trip down to the re-arm point
    frac = Math.max(0, Math.min(1, (lim.pressure_high + 50 - tr.pressure) / (lim.pressure_high + 50 - lim.pressure_rearm))) * 0.9;
    eta = tr.pressure > lim.pressure_rearm ? `stabilizing · ${fx(tr.pressure - lim.pressure_rearm, 0)} kPa to re-arm band` : "stabilizing level";
  }
  $("recProg").style.width = `${(frac * 100).toFixed(0)}%`;
  $("recEta").textContent = eta;
}

function renderConsoleStrip(s, eff) {
  $("cpP").innerHTML = `${fx(s.truth.pressure, 0)}<small>kPa</small>`;
  $("cpL").innerHTML = `${fx(s.truth.level, 1)}<small>%</small>`;
  $("cpA").textContent = fx(eff, 2); $("cpA").style.color = eff < 0.999 ? stateColor(eff) : "";
  const o = s.observer, pl = o && o.available !== false && o.proposals ? o.proposals.label : null;
  $("cpO").textContent = pl || "–"; $("cpO").style.color = pl && pl !== "normal" ? css("--alert") : pl ? css("--ok") : "";
  $("cpS").textContent = !s.sis.tripped ? "armed" : s.sis.protection_mode === "combined_hold" ? "combined hold" : s.sis.escalated ? "e-stop" : "safe-hold";
  $("cpS").style.color = s.sis.tripped ? css("--warn") : css("--ok");
}

function setConn(ok, txt) {
  window.telemetryWorkspace?.connection(ok, txt);
  $("connDot").className = "dot" + (ok ? " pulse" : " off"); $("connTxt").textContent = txt;
  const live = document.querySelector(".live-label");
  live.classList.toggle("unavailable", !ok || txt !== "live");
  live.lastChild.textContent = ok && txt === "live" ? "Live telemetry" : txt === "stale telemetry" ? "Stale telemetry" : txt === "offline" ? "Offline" : "Connecting";
  if (!ok || txt !== "live") {
    $("sisPill").className = "pill"; $("sisPill").textContent = "SIS UNKNOWN";
    ["summaryPressureState", "summaryAuthorityState"].forEach(id => {
      $(id).textContent = "Last reading"; $(id).className = "metric-tag neutral";
    });
  }
}
let runId = null, connectionIssue = false;

async function poll() {
  try {
    const r = await fetch(`/api/state?h=${lastH}&e=${lastE}`, {cache: "no-store", signal: AbortSignal.timeout(5000)});
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const s = await r.json();
    if ((runId !== null && s.run_id !== runId) || (S && s.t < S.t)) {
      H.length = EV.length = SUS.length = REJ.length = 0;
      lastH = lastE = 0; lastChat = -1; lastDec = null;
      built = false; $("dial").replaceChildren(); $("tank").replaceChildren();
      renderLog();
    }
    runId = s.run_id;
    S = s;
    (s.history || []).forEach(h => { H.push(h); lastH = h[0]; });
    if (H.length > 3600) H.splice(0, H.length - 3600);
    const ev = s.events || [];
    ev.forEach(e => { EV.push(e); lastE = e.id; });
    if (EV.length > 400) EV.splice(0, EV.length - 400);
    if (ev.length && TAB === "analysis") renderLog(ev);
    render(s);
    if (s.status === "error") { connectionIssue = true; showBanner("Engine error: " + s.error); setConn(false, "engine error"); }
    else if (s.status && s.status !== "running") { connectionIssue = true; showBanner(s.status + " …"); setConn(true, s.status); }
    else if (s.telemetry_stale) { connectionIssue = true; showBanner("Plant telemetry is stale. Controls are paused."); setConn(false, "stale telemetry"); }
    else { if (connectionIssue) showBanner(""); connectionIssue = false; setConn(true, "live"); }
  } catch (e) {
    connectionIssue = true;
    showBanner("Control room server not reachable (is `python -m live` running?)");
    setConn(false, "offline");
  }
  setTimeout(poll, 500);
}

const inFilter = e => FILTER === "all" || (GROUP[FILTER] || []).includes(e.kind);
function logItem(e) {
  const li = document.createElement("li");
  li.className = e.kind;
  li.innerHTML = `<span class="t">${e.t.toFixed(0)} s</span><span class="k">${e.kind}</span><span></span>`;
  /** @type {HTMLElement} */ (li.lastChild).textContent = e.msg;
  return li;
}
/** full re-render on filter/tab change; otherwise prepend only the new events */
function renderLog(newEvents) {
  const log = $("log");
  if (!newEvents) {
    log.innerHTML = "";
    EV.filter(inFilter).slice(-250).reverse().forEach(e => log.appendChild(logItem(e)));
  } else {
    newEvents.filter(inFilter).forEach(e => log.prepend(logItem(e)));
    while (log.children.length > 250) /** @type {HTMLElement} */ (log.lastChild).remove();
  }
  if (!log.children.length) log.innerHTML = `<li class="empty">No events in this view yet.</li>`;
  else log.querySelectorAll(".empty").forEach(x => x.remove());
  $("evcount").textContent = `${EV.length} events`;
}

const level = (el_, cls) => { el_.classList.remove("ok", "warn", "alert", "info", "off"); if (cls) el_.classList.add(cls); };
function setBar(id, v) { const b = $(id); b.style.width = `${Math.max(1.5, Math.min(1, v) * 100)}%`; b.style.background = stateColor(v); }

function renderSummary(s) {
  const p = s.truth.pressure, lim = s.sis.limits;
  const eff = s.brain.effective ?? s.brain.authority;
  $("summaryPressure").textContent = fx(p, 1);
  $("summaryLevel").textContent = fx(s.truth.level, 2);
  $("summaryProduction").textContent = fx(s.measured.product_flow, 1);
  $("summaryAuthority").textContent = fx(eff * 100, 0);
  const pressureState = $("summaryPressureState");
  const unknown = !Number.isFinite(p);
  pressureState.textContent = unknown ? "Unavailable" : s.sis.tripped ? "Safe-hold" : p >= lim.pressure_high ? "High pressure" : p >= lim.pressure_high - 150 ? "Near limit" : "In range";
  pressureState.className = "metric-tag" + (unknown ? " neutral" : p >= lim.pressure_high ? " alert" : s.sis.tripped || p >= lim.pressure_high - 150 ? " warn" : "");
  $("summaryPressureMargin").textContent = unknown ? "Independent channel unavailable" : `${fx(lim.pressure_high - p, 0)} kPa to trip`;
  $("summaryLevelLimits").textContent = `${lim.level_low}–${lim.level_high}% limits`;
  $("summaryProductionTarget").textContent = `${fx(s.targets?.production_rate_sp, 0)} target`;
  $("summaryAuthorityState").textContent = s.sis.tripped ? "SIS in control" : eff >= .999 ? "Full authority" : "Limited";
  $("summaryAuthorityState").className = "metric-tag " + (s.sis.tripped || eff < .999 ? "warn" : "lavender");
  const pressure = OVERVIEW_SIGNAL === "pressure";
  $("trendValue").textContent = fx(pressure ? s.measured.pressure : s.measured.level, pressure ? 1 : 2);
  $("trendUnit").textContent = pressure ? "kPa" : "%";
  $("trendContext").textContent = `Setpoint ${fx(pressure ? s.targets?.pressure_sp : s.targets?.level_sp, pressure ? 1 : 2)} ${pressure ? "kPa" : "%"}`;
}

const overviewTooltip = document.createElement("div");
overviewTooltip.className = "overview-tooltip";
overviewTooltip.hidden = true;
$("cOverview").parentElement.appendChild(overviewTooltip);
$("cOverview").addEventListener("pointermove", e => {
  if (H.length < 2) return;
  const canvas = $("cOverview"), rect = canvas.getBoundingClientRect();
  const end = H[H.length - 1][1], start = Math.max(H[0][1], end - WIN);
  const x = e.clientX - rect.left;
  if (x < 46 || x > rect.width - 8) { overviewTooltip.hidden = true; return; }
  const t = start + (x - 46) / (rect.width - 54) * (end - start);
  const point = H.reduce((best, row) => Math.abs(row[1] - t) < Math.abs(best[1] - t) ? row : best, H[0]);
  const p = OVERVIEW_SIGNAL === "pressure", unit = p ? "kPa" : "%";
  overviewTooltip.textContent = `${Math.floor(point[1] / 60)}:${String(Math.floor(point[1] % 60)).padStart(2, "0")}\nIndependent: ${fx(point[p ? 3 : 5], p ? 1 : 2)} ${unit}\nAI sensor: ${fx(point[p ? 2 : 4], p ? 1 : 2)} ${unit}`;
  overviewTooltip.style.left = `${Math.max(0, Math.min(rect.width - 180, x + 12))}px`;
  overviewTooltip.style.top = `${Math.max(0, e.clientY - rect.top - 75)}px`;
  overviewTooltip.hidden = false;
});
$("cOverview").addEventListener("pointerleave", () => { overviewTooltip.hidden = true; });

function render(s) {
  if (!s.brain) return;
  const eff = s.brain.effective ?? s.brain.authority;
  const keelA = s.keel ? s.keel.authority : 1, govA = s.governance ? s.governance.applied : 1;
  const m = Math.floor(s.t / 60), sec = Math.floor(s.t % 60);
  $("kT").textContent = `${m}:${String(sec).padStart(2, "0")}`;
  renderRecovery(s);
  const o = s.observer, sus = o && o.available !== false ? o.suspicion : null;
  if (sus !== null && (!SUS.length || s.t - SUS[SUS.length - 1][0] >= 1)) { SUS.push([s.t, sus]); if (SUS.length > 600) SUS.shift(); }
  if (!REJ.length || s.t - REJ[REJ.length - 1][0] >= 1) { REJ.push([s.t, s.gate.rejected]); if (REJ.length > 600) REJ.shift(); }
  if (TAB === "overview") {
    renderSummary(s);
    renderInstruments(s);
    renderDashboard(s);
    const col = stateColor(eff), ring = $("ringVal");
    ring.setAttribute("stroke-dashoffset", String(RING_C * (1 - Math.max(0, Math.min(1, eff)))));
    ring.setAttribute("stroke", col); ring.style.color = col;
    $("kAuth").textContent = fx(eff, 2); $("kAuth").style.color = eff < 0.999 ? col : "";
    setBar("bSlider", s.brain.authority); $("vSlider").textContent = fx(s.brain.authority, 2);
    setBar("bKeel", keelA); $("vKeelBar").textContent = fx(keelA, 2);
    setBar("bGov", govA); $("vGovBar").textContent = fx(govA, 2);
    const low = Math.min(s.brain.authority, keelA, govA);
    const who = low >= 0.999 ? null : low === keelA ? "Keel" : low === govA ? "governance" : "the operator cap";
    $("authWhy").innerHTML = s.sis.tripped ? "<b>SIS in control</b> · the AI waits for re-arm." :
      who ? `<b>Limited by ${who}</b>${who === "Keel" && s.keel.reasons && s.keel.reasons.length ? " · " + s.keel.reasons.join(", ") :
        who === "governance" && s.governance && s.governance.label ? " · " + s.governance.label : ""}` :
      "<b>Full authority</b> · every check is green.";
    $("kRej").textContent = s.gate.rejected; $("kRej").style.color = s.gate.rejected ? css("--alert") : "";
    $("kAdm").textContent = `${s.gate.out_of_band} out-of-band`;
    $("tileG").classList.toggle("alert", EV.slice(-60).some(e => e.kind === "gate" && s.t - e.t < 12));
    $("kSus").textContent = sus === null ? "off" : fx(sus, 2);
    $("kSus").style.color = sus ? css("--alert") : "";
    $("kSusd").textContent = sus === null ? "rules only" : `${(o.proposals && o.proposals.label) || "–"}`;
    $("tileO").classList.toggle("alert", !!sus);
    renderStack(s, eff);
  } else if (TAB === "console") {
    renderConsoleStrip(s, eff);
    renderChat(s);
    renderControls(s);
  } else {
    renderBrain(s);
    renderObservers(s);
    $("roP").textContent = `sensor ${fx(s.measured.pressure, 0)} · true ${fx(s.truth.pressure, 0)}`;
    $("roL").textContent = `sensor ${fx(s.measured.level, 2)} · true ${fx(s.truth.level, 2)}`;
    $("roA").textContent = `now ${fx(eff, 2)}`;
    $("roV").textContent = `F1 ${fx(s.valves.f1, 0)} F2 ${fx(s.valves.f2, 0)} PG ${fx(s.valves.purge, 0)} PR ${fx(s.valves.product, 0)}`;
  }
  draw();
}

function renderStack(s, eff) {
  const d = s.decision;
  $("vBrain").textContent = fx(eff, 2);
  $("dBrain").textContent = s.sis.tripped ? "waiting for re-arm" : d ? (d.rationale || "–") : s.brain.label;
  level($("lyBrain"), s.sis.tripped ? "off" : eff <= 0.15 ? "alert" : eff < 0.999 ? "warn" : null);
  const g = s.governance;
  if (g) {
    $("vGov").textContent = fx(g.applied, 2);
    $("dGov").textContent = g.label ? `suspicion ${fx(g.corroborated, 2)} · ${g.label}` :
      g.recovering ? "recovering" : g.applied < 0.999 ? "holding for a clean window" : "no suspicion";
    level($("lyGov"), g.applied <= 0.15 ? "alert" : g.label ? "warn" : g.recovering || g.applied < 0.999 ? "info" : null);
  }
  const o = s.observer;
  if (!o || o.available === false) {
    $("vObs").textContent = "off"; $("dObs").textContent = "rule monitor only"; level($("lyObs"), "off");
  } else {
    const pl = o.proposals && o.proposals.label, tl = o.telemetry && o.telemetry.label;
    $("vObs").textContent = fx(o.suspicion, 2);
    $("dObs").textContent = `proposals ${pl || "–"} · telemetry ${tl || "–"}`;
    level($("lyObs"), PROPOSAL_ATTACK.includes(pl) ? "alert" : tl && tl !== "normal" ? "info" : null);
  }
  const k = s.keel;
  $("vKeel").textContent = fx(k.authority, 2);
  $("dKeel").textContent = k.divert ? "divert" : k.reasons && k.reasons.length ? k.reasons.join(" · ") : k.authority < 0.999 ? "recovering" : "checks pass";
  level($("lyKeel"), k.divert || k.authority <= 0.0 ? "alert" : k.authority < 0.999 || (k.verdict && k.verdict !== "allow") ? "warn" : null);
  $("vGate").textContent = String(s.gate.rejected);
  $("dGate").textContent = s.gate.rejected ? `${s.gate.rejected} rejected · ${s.gate.admitted} admitted` : `${s.gate.admitted} signed commands admitted`;
  level($("lyGate"), EV.slice(-60).some(e => e.kind === "gate" && s.t - e.t < 12) ? "alert" : null);
  $("vSis").textContent = s.sis.tripped ? (s.sis.protection_mode === "combined_hold" ? "ISOLATED" : s.sis.escalated ? "E-STOP" : "HOLD") : "armed";
  $("dSis").textContent = s.sis.tripped ? s.sis.reason : `own sensor · ${fx(s.truth.pressure, 0)} / ${s.sis.limits.pressure_high} kPa`;
  level($("lySis"), s.sis.tripped ? (s.sis.escalated ? "alert" : "warn") : s.truth.pressure >= s.sis.limits.pressure_high - 150 ? "warn" : null);
}

function renderControls(s) {
  $("bOllama").classList.toggle("on", s.brain.kind === "ollama");
  $("bBase").classList.toggle("on", s.brain.kind !== "ollama");
  if (document.activeElement !== $("auth")) { inp("auth").value = String(s.brain.authority); $("authv").textContent = fx(s.brain.authority, 2); }
  const faults = s.faults || {}, sc = s.scenarios || {};
  const fl = Object.entries(faults).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`)
    .concat(Object.entries(sc).map(([k, v]) => `${k.replace(/_/g, " ")}${v.until ? ` (${Math.max(0, v.until - s.t).toFixed(0)} s left)` : ""}`));
  $("faults").textContent = fl.length ? "Active · " + fl.join(" · ") : "";
}

function renderBrain(s) {
  const th = $("think");
  th.textContent = s.sis.tripped ? "not consulted while tripped" : s.brain.inflight ? "thinking…" : `${s.brain.calls ?? "–"} decisions`;
  const d = s.decision;
  if (d) {
    $("sawP").textContent = fx(d.saw.pressure, 1); $("sawL").textContent = fx(d.saw.level, 2);
    $("truP").textContent = fx(d.true && d.true.pressure, 1); $("truL").textContent = fx(d.true && d.true.level, 2);
    const [p, l, f] = d.sp;
    $("spP").textContent = p === null ? "hold" : fx(p, 1); $("spL").textContent = l === null ? "hold" : fx(l, 2);
    $("spF").textContent = f === null ? "hold" : fx(f, 1);
    $("why").textContent = d.rationale || "";
    $("meta").textContent = `t=${d.t.toFixed(0)} s · ${d.outcome} · ${d.latency_s}s` + (d.note_id ? " · under operator instruction" : "");
  }
  const ds = s.decisions || [], newest = ds.length ? ds[ds.length - 1].t : null;
  if (newest !== lastDec) {
    lastDec = newest;
    const dl = $("declist"); dl.innerHTML = "";
    ds.slice().reverse().forEach(x => {
      const li = document.createElement("li");
      li.innerHTML = `<span>t=${x.t.toFixed(0)}</span><span></span>`;
      /** @type {HTMLElement} */ (li.lastChild).textContent = `${x.sp[0] === null ? "hold" : "P " + fx(x.sp[0], 0) + " · L " + fx(x.sp[1], 1) + " · F " + fx(x.sp[2], 0)} | ${x.rationale}`;
      dl.appendChild(li);
    });
  }
}

function obsCard(card, lab, rsn, meta, v, s, isProposal) {
  if (!v) { level($(card), "off"); $(lab).className = "olabel off"; $(lab).textContent = "waiting…"; $(meta).textContent = ""; return; }
  const bad = v.label && v.label !== "normal";
  const cls = !v.label ? "off" : !bad ? "ok" : isProposal ? "alert" : "warn";
  level($(card), cls === "ok" ? null : cls);
  $(lab).className = "olabel " + cls;
  $(lab).textContent = v.label ? `${v.label}${bad ? " · " + fx(v.score, 2) : ""}` : "no verdict";
  $(rsn).textContent = v.label ? v.reason : (v.note || "");
  $(meta).textContent = `${Math.max(0, s.t - v.t).toFixed(0)} s ago · ${fx(v.latency_s, 1)} s${v.llm_label ? " · " + v.source + " (LLM: " + v.llm_label + ")" : ""}`;
}
function renderObservers(s) {
  const o = s.observer;
  if (!o || o.available === false) {
    $("obsModels").textContent = "unavailable · rule monitor only";
    ["ocP", "ocT"].forEach(c => level($(c), "off")); return;
  }
  $("mP").textContent = (o.models && o.models.proposals) || "none";
  $("mT").textContent = (o.models && o.models.telemetry) || "none";
  $("obsModels").textContent = `${o.calls} calls · ${o.failures} failed`;
  obsCard("ocP", "lP", "rP", "tP", o.proposals, s, true);
  obsCard("ocT", "lT", "rT", "tT", o.telemetry, s, false);
}

// ---------- charts ----------
function hexA(col, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(col.trim());
  if (!m) return col;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}
function canvasCtx(id) {
  const c = /** @type {HTMLCanvasElement} */ ($(id)), dpr = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
  const g = /** @type {CanvasRenderingContext2D} */ (c.getContext("2d")); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  return {g, w, h};
}
function chart(id, series, lines, fixed, minSpan = 0, digits = null) {
  const settings = window.telemetryWorkspace?.settings;
  if (settings?.limits === false) lines = [];
  if (settings?.setpoints === false) series = series.filter(s => s.i !== 10 && s.i !== 11);
  const {g, w, h} = canvasCtx(id);
  if (!H.length || !w) return;
  const tEnd = H[H.length - 1][1], tStart = Math.max(H[0][1], tEnd - WIN);
  const pts = H.filter(p => p[1] >= tStart);
  let lo = Infinity, hi = -Infinity;
  if (fixed) [lo, hi] = fixed; else {
    pts.forEach(p => series.forEach(s => { const v = p[s.i]; if (v !== null && v !== undefined) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
    if (!Number.isFinite(lo)) return;
    lines.forEach(l => { if (l.v >= lo - l.near && l.v <= hi + l.near) { lo = Math.min(lo, l.v); hi = Math.max(hi, l.v); } });
    if (hi - lo < minSpan) { const mid = (hi + lo) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
    const pad = Math.max((hi - lo) * 0.12, 0.5); lo -= pad; hi += pad;
  }
  const L = 46, R = 8, T = 8, B = 22;
  const X = t => L + (t - tStart) / Math.max(1, tEnd - tStart) * (w - L - R);
  const Y = v => T + (1 - (v - lo) / (hi - lo)) * (h - T - B);
  g.font = "12px 'Geist Mono', monospace"; g.fillStyle = css("--muted");
  g.strokeStyle = css("--grid"); g.lineWidth = 1;
  for (let k = 0; k <= 4; k++) {
    const v = lo + (hi - lo) * k / 4, y = Y(v);
    g.beginPath(); g.moveTo(L, y); g.lineTo(w - R, y); g.stroke();
    g.textAlign = "right"; g.fillText(v.toFixed(digits ?? (hi - lo < 10 ? 1 : 0)), L - 8, y + 4);
  }
  g.textAlign = "center";
  const span = tEnd - tStart, step = span > 900 ? 300 : span > 300 ? 120 : 30;
  const mmss = t => `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}`;
  for (let t = Math.ceil(tStart / step) * step; t <= tEnd; t += step) {
    const x = X(t); if (x < L + 14 || x > w - R - 16) continue;
    g.fillText(mmss(t), x, h - 6);
  }
  g.fillStyle = css("--tripbg"); let s0 = null;
  pts.forEach((p, i) => { if (p[12] && s0 === null) s0 = p[1]; if ((!p[12] || i === pts.length - 1) && s0 !== null) { g.fillRect(X(s0), T, X(p[1]) - X(s0), h - T - B); s0 = null; } });
  lines.forEach(l => { if (l.v < lo || l.v > hi) return; g.strokeStyle = hexA(css("--alert"), .75); g.setLineDash([5, 4]); g.beginPath(); g.moveTo(L, Y(l.v)); g.lineTo(w - R, Y(l.v)); g.stroke(); g.setLineDash([]); });
  series.forEach(s => {
    const col = s.c.startsWith("--") ? css(s.c) : s.c;
    const path = () => { g.beginPath(); let started = false, x0 = 0, xl = 0;
      pts.forEach(p => { const v = p[s.i]; if (v === null || v === undefined) { started = false; return; }
        const x = X(p[1]), y = Y(Math.max(lo, Math.min(hi, v))); if (!started) { g.moveTo(x, y); x0 = x; } else g.lineTo(x, y); xl = x; started = true; });
      return [x0, xl]; };
    if (s.fill) {
      const [x0, xl] = path(); g.lineTo(xl, h - B); g.lineTo(x0, h - B); g.closePath();
      const grd = g.createLinearGradient(0, T, 0, h - B); grd.addColorStop(0, hexA(col, .14)); grd.addColorStop(1, hexA(col, 0));
      g.fillStyle = grd; g.fill();
    }
    g.lineJoin = "round"; g.strokeStyle = col; g.lineWidth = s.w || 2; g.setLineDash(s.dash || []);
    path(); g.stroke(); g.setLineDash([]); g.shadowBlur = 0;
  });
}
/** tiny sparkline at the bottom of a KPI tile: last 2 min */
function spark(id, pts, col, minSpan = 1, fixed = null) {
  const {g, w, h} = canvasCtx(id);
  if (pts.length < 2 || !w) return;
  let lo = Math.min(...pts.map(p => p[1])), hi = Math.max(...pts.map(p => p[1]));
  if (fixed) [lo, hi] = fixed;
  else if (hi - lo < minSpan) { const mid = (hi + lo) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const X = t => (t - t0) / Math.max(1, t1 - t0) * w, Y = v => h - 6 - (v - lo) / (hi - lo) * (h - 14);
  const line = new Path2D(); pts.forEach((p, i) => i ? line.lineTo(X(p[0]), Y(p[1])) : line.moveTo(X(p[0]), Y(p[1])));
  const area = new Path2D(line); area.lineTo(X(t1), h); area.lineTo(0, h); area.closePath();
  const grd = g.createLinearGradient(0, 0, 0, h); grd.addColorStop(0, hexA(col, .25)); grd.addColorStop(1, hexA(col, 0));
  g.fillStyle = grd; g.fill(area);
  g.strokeStyle = col; g.lineWidth = 1.6; g.stroke(line);
}
function draw() {
  if (!S || !S.sis) return;
  const lim = S.sis.limits;
  if (TAB === "analysis") {
    window.telemetryWorkspace?.render(S);
    chart("cP", [{i: 10, c: "--sp", dash: [4, 3], w: 1.3}, {i: 2, c: "--meas"}, {i: 3, c: "--truth", fill: true}], [{v: lim.pressure_high, near: 150}], null, 60);
    chart("cL", [{i: 11, c: "--sp", dash: [4, 3], w: 1.3}, {i: 4, c: "--meas"}, {i: 5, c: "--truth", fill: true}], [{v: lim.level_low, near: 6}, {v: lim.level_high, near: 6}], null, 3);
    chart("cA", [{i: 14, c: "--info", dash: [5, 3], w: 1.3}, {i: 15, c: "--accent2", dash: [2, 3], w: 1.5}, {i: 13, c: "--auth", w: 2.2, fill: true}], [{v: 0.1, near: 1}], [0, 1.05], 0, 2);
    chart("cV", [{i: 6, c: "#b4a1ff"}, {i: 7, c: "#69d6c7"}, {i: 8, c: "#e7bc72"}, {i: 9, c: "#e58baa"}], [], [0, 100]);
  } else if (TAB === "overview") {
    const pressure = OVERVIEW_SIGNAL === "pressure";
    chart("cOverview", [{i: pressure ? 10 : 11, c: "--sp", dash: [4, 4], w: 1.2},
      {i: pressure ? 3 : 5, c: "--truth", fill: true, w: 2},
      {i: pressure ? 2 : 4, c: "--meas", dash: [3, 3], w: 1.3}],
      pressure ? [{v: lim.pressure_high, near: 150}] : [{v: lim.level_low, near: 3}, {v: lim.level_high, near: 3}], null, pressure ? 15 : 1);
    $("chartEmpty").hidden = H.length >= 2;
    const tEnd = H.length ? H[H.length - 1][1] : 0;
    spark("sA", REJ.filter(p => p[0] >= tEnd - 120), S.gate.rejected ? css("--alert") : css("--ok"), 4);
    spark("sV", SUS.filter(p => p[0] >= tEnd - 120), css("--accent2"), 1, [0, 1.08]);
  }
}
window.addEventListener("resize", draw);
let saved = "overview";
try { saved = localStorage.getItem("scram.tab") || "overview"; } catch (e) { /* storage unavailable */ }
setTab(["overview", "console", "analysis"].includes(saved) ? saved : "overview");
poll();
