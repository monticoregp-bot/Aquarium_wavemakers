/* Aquarium Wavemaker cards
 *
 *  1. Editor (full card, for a config dashboard)
 *       type: custom:aquarium-wavemaker-card
 *       title: Wavemakers                           # optional
 *       master_entity: input_boolean.reef_waves     # optional with one aquarium
 *
 *  2. Summary (compact, for a main display): what each pump is doing now, what it
 *     follows, the next change and a 24 h timeline per pump
 *       type: custom:aquarium-wavemaker-summary-card
 *       title: Flow                                 # optional
 *       master_entity: input_boolean.reef_waves     # optional with one aquarium
 *       pumps: [pump_1, pump_2]                     # optional: only these pumps
 *
 * With several aquariums set master_entity to pick which one a card shows.
 * The waveform preview comes from the same engine that the schedule uses.
 */
(() => {
"use strict";

const DAY = 1440;
const MODES = [
  ["wave", "Wave (sine)"],
  ["pulse", "Pulse"],
  ["surge", "Surge (ramp up)"],
  ["random", "Random"],
  ["constant", "Constant"],
];
const MODE_COLOR = { wave: "#3b82f6", pulse: "#f59e0b", surge: "#ef4444", random: "#a855f7", constant: "#22c55e" };
const RELATIONS = [
  ["slave", "Slave: same as the source"],
  ["inverse", "Inverse: opposite phase"],
];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const toMin = (hhmm) => {
  const [h, m] = String(hhmm).split(":").map(Number);
  return h * 60 + m;
};
const fmtMin = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const num = (v, d = 1) => (Number.isFinite(+v) ? +(+v).toFixed(d) : "-");
const hhmm = (iso) => (iso ? iso.slice(11, 16) : "-");
const when = (iso, nowIso) => {
  if (!iso) return "-";
  const day = iso.slice(0, 10), today = nowIso.slice(0, 10);
  if (day === today) return hhmm(iso);
  const tomorrow = new Date(Date.parse(today + "T12:00:00Z") + 864e5).toISOString().slice(0, 10);
  return day === tomorrow ? `tomorrow ${hhmm(iso)}` : `${day.slice(5)} ${hhmm(iso)}`;
};

const CSS_COMMON = `
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px;
          background: var(--secondary-background-color); font-size: .85rem; }
  .chip i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  svg { width: 100%; height: auto; display: block; }
  .err { color: var(--error-color); font-size: .85rem; min-height: 1.2em; }
  .muted { color: var(--secondary-text-color); font-size: .85rem; }
  .legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: .8rem; color: var(--secondary-text-color); margin-top: 4px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }`;

const LEGEND = `<div class="legend">${MODES.map(([m, l]) => `<span><i style="background:${MODE_COLOR[m]}"></i>${l.split(" ")[0].toLowerCase()}</span>`).join("")}
  <span><i style="background:repeating-linear-gradient(45deg,var(--secondary-text-color) 0 2px,transparent 2px 5px)"></i>follows another pump</span></div>`;

/* ----------------------------------------------------------------- timeline */
const segments = (startMin, lenMin) => (startMin + lenMin <= DAY ? [[startMin, startMin + lenMin]] : [[startMin, DAY], [0, startMin + lenMin - DAY]]);
const spanLen = (s, e) => (((e - s) % DAY) + DAY) % DAY || DAY;

/** 24 h strip: each block as a band between its min and max, follow windows hatched. */
function timelineSvg(cfg, nowMin, { height = 130, labels = true } = {}) {
  const W = 720, L = labels ? 34 : 8, R = 8, T = 8, B = labels ? 22 : 6, H = height;
  const x = (m) => L + (m / DAY) * (W - L - R);
  const y = (v) => T + (1 - v / 100) * (H - T - B);
  let out = `<defs><pattern id="wmhatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
    <line x1="0" y1="0" x2="0" y2="7" stroke="var(--secondary-text-color)" stroke-width="2" opacity=".45"/></pattern></defs>`;
  for (let h = 0; h <= 24; h += 3) {
    out += `<line x1="${x(h * 60)}" x2="${x(h * 60)}" y1="${T}" y2="${H - B}" stroke="var(--divider-color)" stroke-width="1"/>`;
    if (labels) out += `<text x="${x(h * 60)}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--secondary-text-color)">${String(h % 24).padStart(2, "0")}:00</text>`;
  }
  if (labels) {
    for (const v of [0, 50, 100]) {
      out += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--divider-color)" stroke-width="1" stroke-dasharray="2 4"/>
              <text x="${L - 5}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--secondary-text-color)">${v}%</text>`;
    }
  }
  // default level shows through wherever no block is scheduled
  out += `<line x1="${L}" x2="${W - R}" y1="${y(cfg.default_level)}" y2="${y(cfg.default_level)}" stroke="var(--secondary-text-color)" stroke-width="1.5" stroke-dasharray="5 4" opacity=".6"/>`;
  for (const b of cfg.blocks || []) {
    const s = toMin(b.start), len = spanLen(s, toMin(b.end));
    for (const [a, z] of segments(s, len)) {
      const w = x(z) - x(a);
      const flat = b.mode === "constant";
      const top = flat ? y(b.level) - 3 : y(b.high);
      const hgt = flat ? 6 : Math.max(4, y(b.low) - y(b.high));
      out += `<rect x="${x(a)}" y="${top}" width="${w}" height="${hgt}" rx="2" fill="${MODE_COLOR[b.mode]}" opacity="${flat ? 0.95 : 0.6}"><title>${b.start}-${b.end} ${b.mode}</title></rect>`;
      if (labels && w > 64) out += `<text x="${x(a) + w / 2}" y="${Math.max(top - 4, T + 10)}" text-anchor="middle" font-size="11" fill="var(--primary-text-color)">${b.mode}</text>`;
    }
  }
  if (cfg.follow_source) {
    const wins = cfg.follow_all_day ? [[0, DAY]] : (cfg.follow_windows || []).map(([s, e]) => [toMin(s), spanLen(toMin(s), toMin(e))]);
    let widest = null;
    for (const [s, len] of wins) {
      for (const [a, z] of segments(cfg.follow_all_day ? 0 : s, cfg.follow_all_day ? DAY : len)) {
        out += `<rect x="${x(a)}" y="${T}" width="${x(z) - x(a)}" height="${H - T - B}" fill="var(--card-background-color)" opacity=".55"/>
                <rect x="${x(a)}" y="${T}" width="${x(z) - x(a)}" height="${H - T - B}" fill="url(#wmhatch)" stroke="var(--secondary-text-color)" stroke-width="1" opacity=".9"/>`;
        if (!widest || z - a > widest[1] - widest[0]) widest = [a, z];
      }
    }
    if (widest && x(widest[1]) - x(widest[0]) > 90) {
      const label = `follows ${cfg.follow_source}${cfg.follow_relation === "inverse" ? " (inverse)" : ""}`;
      out += `<text x="${(x(widest[0]) + x(widest[1])) / 2}" y="${T + (H - T - B) / 2}" text-anchor="middle" font-size="12" fill="var(--primary-text-color)" font-weight="600">${esc(label)}</text>`;
    }
  }
  out += `<line x1="${x(nowMin)}" x2="${x(nowMin)}" y1="${T}" y2="${H - B}" stroke="var(--primary-text-color)" stroke-width="1.5" stroke-dasharray="4 3"/>`;
  return out;
}

/** A few seconds of one block's waveform (values 0..100 over `duration` seconds). */
function waveSvg(wave) {
  const W = 720, H = 130, L = 34, R = 8, T = 8, B = 22;
  const x = (t) => L + (t / wave.duration) * (W - L - R);
  const y = (v) => T + (1 - v / 100) * (H - T - B);
  let out = "";
  for (const v of [0, 50, 100]) {
    out += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--divider-color)" stroke-width="1" stroke-dasharray="2 4"/>
            <text x="${L - 5}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--secondary-text-color)">${v}%</text>`;
  }
  const ticks = 6;
  for (let i = 0; i <= ticks; i++) {
    const t = (wave.duration * i) / ticks;
    out += `<text x="${x(t)}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--secondary-text-color)">${num(t, 0)} s</text>`;
  }
  const n = wave.values.length;
  const d = wave.values.map((v, i) => `${i ? "L" : "M"}${x((wave.duration * i) / (n - 1)).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  out += `<path d="${d}" fill="none" stroke="${MODE_COLOR[wave.mode]}" stroke-width="2.5" stroke-linejoin="round"/>`;
  return out;
}

const summary = (p) => p.current.description;

/* =========================================================== editor card */
class AquariumWavemakerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._sel = null;
    this._draft = null;
    this._dirty = false;
    this._index = 0;
    this._wave = null;
    this._error = "";
    this._manual = { mode: "wave", low: 20, high: 80, level: 50, period: 10, pulse: 5, interval: 10, fade: 5 };
  }

  setConfig(config) {
    this._config = { title: "Aquarium Wavemaker", ...config };
  }
  getCardSize() {
    return 14;
  }
  static getStubConfig() {
    return {};
  }
  connectedCallback() {
    if (this._hass && this._data) this._poll();
  }
  disconnectedCallback() {
    clearTimeout(this._pollTimer);
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._shell();
      this._load();
    }
    this._syncMaster();
  }

  /* ------------------------------------------------------------ data */
  _ws(msg) {
    if (this._config.master_entity) msg.master_entity = this._config.master_entity;
    return this._hass.callWS(msg);
  }

  async _load() {
    try {
      this._data = await this._ws({ type: "aquarium_wavemaker/get" });
    } catch (e) {
      this._root("fatal").textContent = `Could not reach the Aquarium Wavemaker integration: ${e.message || e.code || e}`;
      this._poll();
      return;
    }
    this._root("fatal").textContent = "";
    const names = Object.keys(this._data.pumps);
    if (!this._sel || !names.includes(this._sel)) {
      this._sel = names[0] || null;
      this._dirty = false;
    }
    if (!this._dirty) this._resetDraft();
    this._syncMaster();
    this._renderTabs();
    this._renderStatus();
    if (!this._dirty || !this._formBuilt) this._renderForm();
    this._drawTimeline();
    this._requestPreview();
    this._poll();
  }

  _poll() {
    clearTimeout(this._pollTimer);
    this._pollTimer = setTimeout(() => this._load(), 30000);
  }

  _pump() {
    return this._data?.pumps?.[this._sel];
  }

  _resetDraft() {
    const p = this._pump();
    this._draft = p ? JSON.parse(JSON.stringify(p.config)) : null;
    this._dirty = false;
    this._index = 0;
  }

  _payload() {
    const d = this._draft;
    const n = (v) => (v === "" || v == null ? 0 : Number(v));
    return {
      service: d.service || "", channel: n(d.channel), floor: n(d.floor), cap: n(d.cap), slew: n(d.slew),
      fade_seconds: n(d.fade_seconds), default_level: n(d.default_level),
      follow_source: d.follow_source || "", follow_relation: d.follow_relation, follow_all_day: !!d.follow_all_day,
      follow_windows: d.follow_windows || [], follow_constant: d.follow_constant,
      blocks: (d.blocks || []).map((b) => ({
        start: b.start, end: b.end, mode: b.mode, low: n(b.low), high: n(b.high), level: n(b.level),
        period: n(b.period), pulse: n(b.pulse), interval: n(b.interval),
      })),
    };
  }

  _requestPreview() {
    clearTimeout(this._previewTimer);
    if (!this._draft) {
      this._wave = null;
      this._drawWave();
      return;
    }
    this._previewTimer = setTimeout(async () => {
      try {
        const r = await this._ws({ type: "aquarium_wavemaker/preview", name: this._sel, config: this._payload(), index: this._index });
        this._wave = r.wave;
        this._error = "";
      } catch (e) {
        this._wave = null;
        this._error = e.message || String(e);
      }
      this._drawWave();
    }, 150);
  }

  /* ----------------------------------------------------------- actions */
  async _call(msg, okText) {
    try {
      const snap = await this._ws(msg);
      if (okText) this._status(okText);
      return snap;
    } catch (e) {
      this._error = e.message || String(e);
      this._drawWave();
      return null;
    }
  }

  async _save() {
    if (await this._call({ type: "aquarium_wavemaker/set_pump", name: this._sel, config: this._payload() }, "Saved")) {
      this._dirty = false;
      this._error = "";
      await this._load();
    }
  }

  async _addPump() {
    const raw = prompt("Pump name (letters, numbers, underscores), e.g. pump_2, left, rear");
    if (!raw) return;
    const name = raw.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").slice(0, 32);
    if (!name) return;
    const first = this._pump();
    const config = { service: first?.config.service || "", channel: 0, blocks: [] };
    if (await this._call({ type: "aquarium_wavemaker/set_pump", name, config }, "Pump added")) {
      this._sel = name;
      this._dirty = false;
      await this._load();
      this._renderForm();
    }
  }

  async _deletePump() {
    if (!confirm(`Delete pump "${this._sel}" and its schedule?`)) return;
    if (await this._call({ type: "aquarium_wavemaker/delete_pump", name: this._sel }, "Deleted")) {
      this._sel = null;
      this._dirty = false;
      await this._load();
      this._renderForm();
    }
  }

  _selectPump(name) {
    if (name === this._sel) return;
    if (this._dirty && !confirm("Discard unsaved changes?")) return;
    this._sel = name;
    this._resetDraft();
    this._error = "";
    this._renderTabs();
    this._renderStatus();
    this._renderForm();
    this._drawTimeline();
    this._requestPreview();
  }

  async _sendManual() {
    const m = this._manual;
    const wave = { mode: m.mode, low: +m.low, high: +m.high, level: +m.level, period: +m.period, pulse: +m.pulse, interval: +m.interval, fade: +m.fade };
    if (await this._call({ type: "aquarium_wavemaker/set_wave", name: this._sel, wave }, "Sent. Switch the master toggle off to hold it; the schedule replaces it within a minute otherwise")) {
      this._error = "";
    }
  }

  async _toggleMaster(on) {
    const entity = this._masterEntity();
    try {
      await this._hass.callService("input_boolean", on ? "turn_on" : "turn_off", { entity_id: entity });
    } catch (e) {
      this._status(`Could not toggle ${entity}: ${e.message || e}`);
    }
  }

  _markDirty() {
    this._dirty = true;
    const s = this._root("save"), r = this._root("revert");
    if (s) s.disabled = false;
    if (r) r.disabled = false;
  }

  _changed(rerender) {
    this._markDirty();
    if (rerender) this._renderForm();
    this._drawTimeline();
    this._requestPreview();
  }

  /* ------------------------------------------------------------ events */
  _onInput(e) {
    const t = e.target;
    if (!this._draft) return;
    const val = t.type === "checkbox" ? t.checked : t.type === "number" ? (t.value === "" ? "" : Number(t.value)) : t.value;
    if (t.dataset.k) {
      const k = t.dataset.k;
      this._draft[k] = val;
      this._changed(["follow_source", "follow_all_day", "follow_relation"].includes(k));
    } else if (t.dataset.b) {
      const [i, f] = t.dataset.b.split(":");
      this._draft.blocks[Number(i)][f] = val;
      this._index = Number(i);
      this._changed(f === "mode");
    } else if (t.dataset.w) {
      const [i, j] = t.dataset.w.split(":");
      this._draft.follow_windows[Number(i)][Number(j)] = val;
      this._changed(false);
    } else if (t.dataset.m) {
      this._manual[t.dataset.m] = val;
      if (t.dataset.m === "mode") this._renderForm();
    }
  }

  _onClick(e) {
    const btn = e.target.closest("[data-act]");
    if (!btn || btn.disabled) return;
    const [act, arg] = btn.dataset.act.split(":");
    const d = this._draft;
    if (act === "save") this._save();
    else if (act === "revert") {
      this._resetDraft();
      this._error = "";
      this._renderForm();
      this._drawTimeline();
      this._requestPreview();
    } else if (act === "delete") this._deletePump();
    else if (act === "add") this._addPump();
    else if (act === "tab") this._selectPump(arg);
    else if (act === "send") this._sendManual();
    else if (act === "addblock") {
      const last = d.blocks[d.blocks.length - 1];
      const start = last ? last.end : "00:00";
      const end = fmtMin((toMin(start) + 60) % DAY);
      d.blocks.push({ start, end, mode: "wave", low: 20, high: 80, level: 50, period: 10, pulse: 5, interval: 10 });
      this._index = d.blocks.length - 1;
      this._changed(true);
    } else if (act === "delblock") {
      d.blocks.splice(Number(arg), 1);
      this._index = 0;
      this._changed(true);
    } else if (act === "pick") {
      this._index = Number(arg);
      this._renderForm();
      this._requestPreview();
    } else if (act === "addwin") {
      d.follow_windows.push(["06:00", "12:00"]);
      this._changed(true);
    } else if (act === "delwin") {
      d.follow_windows.splice(Number(arg), 1);
      this._changed(true);
    }
  }

  /* --------------------------------------------------------- rendering */
  _root(id) {
    return this.shadowRoot.getElementById(id);
  }

  _status(msg) {
    const el = this._root("status");
    if (!el) return;
    el.textContent = msg;
    clearTimeout(this._statusTimer);
    this._statusTimer = setTimeout(() => (el.textContent = ""), 6000);
  }

  _masterEntity() {
    return this._config.master_entity || this._data?.master_entity || "input_boolean.aquarium_wavemaker";
  }

  _shell() {
    this.shadowRoot.innerHTML = `
      <style>
        ${CSS_COMMON}
        ha-card { padding: 16px; display: block; }
        .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
        .head h2 { margin: 0; font-size: 1.25rem; font-weight: 500; }
        .master { display: flex; align-items: center; gap: 8px; color: var(--secondary-text-color); font-size: .9rem; }
        .tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 14px 0 10px; }
        button { font: inherit; cursor: pointer; border-radius: 8px; padding: 6px 14px; border: 1px solid var(--divider-color);
                 background: transparent; color: var(--primary-text-color); }
        button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        button.sel, button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
        button.danger { color: var(--error-color); }
        button:disabled { opacity: .45; cursor: default; }
        fieldset { border: 1px solid var(--divider-color); border-radius: 10px; margin: 12px 0 0; padding: 10px 12px 12px; min-width: 0; }
        fieldset.locked { opacity: .55; }
        legend { padding: 0 6px; font-size: .85rem; color: var(--secondary-text-color); }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
        label { display: flex; flex-direction: column; gap: 4px; font-size: .8rem; color: var(--secondary-text-color); }
        label.inline { flex-direction: row; align-items: center; gap: 8px; font-size: .85rem; }
        input, select { font: inherit; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--divider-color);
                        background: var(--card-background-color); color: var(--primary-text-color); min-width: 0; }
        input:disabled, select:disabled { opacity: .5; }
        .block { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 8px; align-items: end;
                 padding: 10px 0; border-top: 1px solid var(--divider-color); }
        .block:first-of-type { border-top: 0; }
        .block.picked { box-shadow: -3px 0 0 var(--primary-color); padding-left: 8px; }
        .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; align-items: center; }
        .spacer { flex: 1; }
        .banner { margin: 10px 0 0; padding: 10px 12px; border-radius: 8px; background: var(--secondary-background-color); font-size: .9rem; }
        #fatal { color: var(--error-color); }
      </style>
      <ha-card>
        <div class="head">
          <h2>${esc(this._config.title)}</h2>
          <label class="master"><span id="masterlabel">Wavemaker</span><ha-switch id="master"></ha-switch></label>
        </div>
        <div id="fatal"></div>
        <div class="tabs" id="tabs"></div>
        <div id="statusbox"></div>
        <div id="form"></div>
        <div class="muted" style="margin-top:14px">24 h schedule: bars show each block's minimum to maximum, the dashed line is the default level.</div>
        <svg id="timeline" viewBox="0 0 720 130" role="img" aria-label="Wave schedule over 24 hours"></svg>
        ${LEGEND}
        <div class="muted" style="margin-top:12px" id="wavetitle"></div>
        <svg id="wave" viewBox="0 0 720 130" role="img" aria-label="Waveform of the selected block"></svg>
        <div class="err" id="err"></div>
        <div class="muted" id="status"></div>
      </ha-card>`;
    const sw = this._root("master");
    sw.addEventListener("change", () => this._toggleMaster(sw.checked));
    this._root("form").addEventListener("input", (e) => this._onInput(e));
    this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
  }

  _syncMaster() {
    if (!this._root("master")) return;
    const entity = this._masterEntity();
    const st = this._hass.states[entity];
    const sw = this._root("master");
    sw.checked = st?.state === "on";
    sw.disabled = !st;
    this._root("masterlabel").textContent = !st
      ? `Helper ${entity} not found. Create a Toggle helper with that entity id`
      : st.state === "on"
      ? "Schedule driving the pumps"
      : "Schedule off: pumps keep their last wave";
  }

  _renderTabs() {
    const names = Object.keys(this._data.pumps);
    this._root("tabs").innerHTML =
      names.map((n) => `<button data-act="tab:${esc(n)}" class="${n === this._sel ? "sel" : ""}">${esc(n)}</button>`).join("") +
      `<button data-act="add">+ Add pump</button>`;
  }

  _renderStatus() {
    const p = this._pump();
    const box = this._root("statusbox");
    if (!p) {
      box.innerHTML = "";
      return;
    }
    const cur = p.current, now = this._data.now;
    box.innerHTML = `<div class="row">
      <span class="chip"><i style="background:${MODE_COLOR[cur.mode]}"></i>${esc(summary(p))}</span>
      ${p.following ? `<span class="chip">Following ${esc(p.following)}${p.config.follow_relation === "inverse" ? " (inverse)" : ""}</span>` : ""}
      <span class="chip">Next change ${esc(when(p.next_change, now))}</span>
      ${p.followers.length ? `<span class="chip">Followed by ${esc(p.followers.join(", "))}</span>` : ""}</div>`;
  }

  _paramFields(prefix, o) {
    const f = (key, label, extra = "") => `<label>${label}<input ${prefix}="${key}" type="number" value="${esc(o[key])}" ${extra}></label>`;
    const range = f("low", "Min %", 'min="0" max="100" step="1"') + f("high", "Max %", 'min="0" max="100" step="1"');
    switch (o.mode) {
      case "constant": return f("level", "Level %", 'min="0" max="100" step="1"');
      case "pulse": return range + f("period", "Period (s)", 'min="1" step="0.5"') + f("pulse", "Pulse time (s)", 'min="0.1" step="0.5"');
      case "random": return range + f("interval", "Change every (s)", 'min="1" step="1"');
      default: return range + f("period", "Period (s)", 'min="1" step="0.5"');
    }
  }

  _renderForm() {
    const el = this._root("form");
    const d = this._draft;
    this._formBuilt = true;
    if (!d) {
      el.innerHTML = `<p class="muted">No pumps yet. Add one to start building a schedule.</p>`;
      return;
    }
    const others = Object.keys(this._data.pumps).filter((n) => n !== this._sel);
    const locked = !!d.follow_source && d.follow_all_day;
    const modeSel = (attr, cur) => `<label>Mode<select ${attr}>${MODES.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${l}</option>`).join("")}</select></label>`;
    const f = (k, label, type = "number", extra = "") => `<label>${label}<input data-k="${k}" type="${type}" value="${esc(d[k])}" ${extra}></label>`;

    const follow = `<fieldset><legend>Follow another pump</legend>
      <div class="grid">
        <label>Follows<select data-k="follow_source"><option value="">none (own schedule)</option>${others.map((n) => `<option value="${esc(n)}" ${n === d.follow_source ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
        <label>Relation<select data-k="follow_relation" ${d.follow_source ? "" : "disabled"}>${RELATIONS.map(([v, l]) => `<option value="${v}" ${v === d.follow_relation ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      </div>
      ${d.follow_source ? `<div class="row" style="margin-top:8px">
        <label class="inline"><input data-k="follow_all_day" type="checkbox" ${d.follow_all_day ? "checked" : ""}> Follow all day</label>
        ${d.follow_relation === "inverse" ? `<label class="inline"><input data-k="follow_constant" type="checkbox" ${d.follow_constant === "invert" ? "checked" : ""}> In constant mode use 100% minus the level (off: mirror the level)</label>` : ""}</div>` : ""}
      ${d.follow_source && !d.follow_all_day ? `<div style="margin-top:8px">${(d.follow_windows || []).map((w, i) => `<div class="row" style="margin-bottom:6px">
          <input type="time" data-w="${i}:0" value="${esc(w[0])}"> to <input type="time" data-w="${i}:1" value="${esc(w[1])}">
          <button data-act="delwin:${i}">Remove</button></div>`).join("")}
        <button data-act="addwin">Add follow window</button>
        <div class="muted">During these times this pump follows ${esc(d.follow_source)}. Its own blocks cannot cover them: those times are locked.</div></div>` : ""}
      ${locked ? `<div class="banner">This pump follows <b>${esc(d.follow_source)}</b> all day${d.follow_relation === "inverse" ? ", in opposite phase" : ""}, so its own schedule is locked. Motor limits below still apply to this pump.</div>` : ""}
    </fieldset>`;

    const blocks = (d.blocks || []).map((b, i) => `<div class="block ${i === this._index ? "picked" : ""}">
        <label>Start<input type="time" data-b="${i}:start" value="${esc(b.start)}"></label>
        <label>End<input type="time" data-b="${i}:end" value="${esc(b.end)}"></label>
        ${modeSel(`data-b="${i}:mode"`, b.mode)}
        ${this._paramFields("data-b", { ...b }).replace(/data-b="(\w+)"/g, (_, k) => `data-b="${i}:${k}"`)}
        <div class="row"><button data-act="pick:${i}" title="Show this block's waveform">Show wave</button><button data-act="delblock:${i}">Remove</button></div></div>`).join("");
    const schedule = `<fieldset class="${locked ? "locked" : ""}" ${locked ? "disabled" : ""}><legend>Schedule${locked ? " (locked: follows another pump)" : ""}</legend>
      ${blocks || `<div class="muted">No blocks yet: the pump runs the default level all day.</div>`}
      <div class="actions"><button data-act="addblock">Add block</button>
        <label class="inline">Default level % (no block active)<input data-k="default_level" type="number" min="0" max="100" step="1" value="${esc(d.default_level)}" style="width:80px"></label></div>
      <div class="muted">A block may cross midnight (for example 21:10 to 06:00). Blocks cannot overlap; start equal to end means the whole day. At each change the pump cross-fades over the cross-fade time below.</div>
    </fieldset>`;

    const motor = `<fieldset><legend>Pump and motor protection</legend>
      <div class="grid">
        <label>ESPHome action<input data-k="service" type="text" value="${esc(d.service)}" placeholder="esphome.wavemaker_set_wave"></label>
        ${f("channel", "Channel on the ESP32", "number", 'min="0" max="15" step="1"')}
        ${f("floor", "Minimum running speed %", "number", 'min="0" max="100" step="1"')}
        ${f("cap", "Maximum speed %", "number", 'min="0" max="100" step="1"')}
        ${f("slew", "Ramp limit (% per second)", "number", 'min="1" max="1000" step="1"')}
        ${f("fade_seconds", "Cross-fade time (s)", "number", 'min="0" max="600" step="1"')}
      </div>
      <div class="muted">Computed speeds below the minimum run at the minimum (a computed 0 means off); nothing exceeds the maximum; the ramp limit stops the motor jumping, for example at pulse edges.</div>
    </fieldset>`;

    const m = this._manual;
    const manual = `<fieldset><legend>Test wave (not saved)</legend>
      <div class="grid">${modeSel('data-m="mode"', m.mode)}${this._paramFields("data-m", m)}
        <label>Cross-fade (s)<input data-m="fade" type="number" min="0" max="600" value="${esc(m.fade)}"></label></div>
      <div class="actions"><button data-act="send">Send to ${esc(this._sel)}</button>
        <span class="muted">While the master toggle is on the schedule replaces this within a minute.</span></div>
    </fieldset>`;

    el.innerHTML = `${follow}${schedule}${motor}${manual}
      <div class="actions">
        <button class="primary" id="save" data-act="save" ${this._dirty ? "" : "disabled"}>Save</button>
        <button id="revert" data-act="revert" ${this._dirty ? "" : "disabled"}>Revert</button>
        <span class="spacer"></span>
        <button class="danger" data-act="delete">Delete pump</button>
      </div>`;
  }

  _drawTimeline() {
    const svg = this._root("timeline");
    if (!svg || !this._data || !this._draft) {
      if (svg) svg.innerHTML = "";
      return;
    }
    svg.innerHTML = timelineSvg(this._draft, this._data.now_minute);
  }

  _drawWave() {
    const svg = this._root("wave");
    if (!svg) return;
    this._root("err").textContent = this._error;
    const b = this._draft?.blocks?.[this._index];
    this._root("wavetitle").textContent = this._wave && b ? `Waveform of block ${b.start}-${b.end} (${b.mode}), computed by the same engine as the pump firmware` : "";
    svg.innerHTML = this._wave ? waveSvg(this._wave) : "";
  }
}

/* ========================================================== summary card */
class AquariumWavemakerSummaryCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = { title: "", ...config };
  }
  getCardSize() {
    return 4;
  }
  static getStubConfig() {
    return {};
  }
  connectedCallback() {
    if (this._hass && this._built) this._load();
  }
  disconnectedCallback() {
    clearTimeout(this._timer);
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._shell();
      this._load();
    }
  }

  _shell() {
    this._built = true;
    this.shadowRoot.innerHTML = `
      <style>
        ${CSS_COMMON}
        ha-card { padding: 16px; display: block; }
        h2 { margin: 0 0 8px; font-size: 1.25rem; font-weight: 500; }
        .pump { padding: 10px 0; border-top: 1px solid var(--divider-color); }
        .pump:first-of-type { border-top: 0; }
        .name { font-weight: 500; text-transform: capitalize; margin-right: 4px; }
        .paused { color: var(--secondary-text-color); font-size: .85rem; margin-bottom: 6px; }
      </style>
      <ha-card>
        ${this._config.title ? `<h2>${esc(this._config.title)}</h2>` : ""}
        <div id="paused" class="paused"></div>
        <div id="pumps"></div>
        <div class="err" id="err"></div>
      </ha-card>`;
  }

  async _load() {
    clearTimeout(this._timer);
    if (!this._hass || !this._built) return;
    const err = this.shadowRoot.getElementById("err");
    let data;
    try {
      const msg = { type: "aquarium_wavemaker/get" };
      if (this._config.master_entity) msg.master_entity = this._config.master_entity;
      data = await this._hass.callWS(msg);
    } catch (e) {
      err.textContent = `Could not reach the Aquarium Wavemaker integration: ${e.message || e.code || e}`;
      this._timer = setTimeout(() => this._load(), 30000);
      return;
    }
    err.textContent = "";
    let names = Object.keys(data.pumps);
    if (Array.isArray(this._config.pumps) && this._config.pumps.length) names = names.filter((n) => this._config.pumps.includes(n));
    this.shadowRoot.getElementById("paused").textContent = data.master_on ? "" : "Schedule is off: pumps keep their last wave";
    this.shadowRoot.getElementById("pumps").innerHTML =
      names
        .map((n) => {
          const p = data.pumps[n];
          return `<div class="pump">
            <div class="row"><span class="name">${esc(n)}</span>
              <span class="chip"><i style="background:${MODE_COLOR[p.current.mode]}"></i>${esc(summary(p))}</span>
              ${p.following ? `<span class="chip">${p.config.follow_relation === "inverse" ? "inverse of" : "follows"} ${esc(p.following)}</span>` : ""}
              <span class="chip">Next ${esc(when(p.next_change, data.now))}</span></div>
            <svg viewBox="0 0 720 60" role="img" aria-label="${esc(n)} schedule over 24 hours">${timelineSvg(p.config, data.now_minute, { height: 60, labels: false })}</svg></div>`;
        })
        .join("") + (names.length ? LEGEND : "");
    this._timer = setTimeout(() => this._load(), 30000);
  }
}

/* ------------------------------------------------------------ registration */
if (!customElements.get("aquarium-wavemaker-card")) customElements.define("aquarium-wavemaker-card", AquariumWavemakerCard);
if (!customElements.get("aquarium-wavemaker-summary-card")) customElements.define("aquarium-wavemaker-summary-card", AquariumWavemakerSummaryCard);
window.customCards = window.customCards || [];
[
  {
    type: "aquarium-wavemaker-card",
    name: "Aquarium Wavemaker editor",
    description: "Schedule wave modes per pump, with followers and inverse pumps, motor protection and a waveform preview.",
  },
  {
    type: "aquarium-wavemaker-summary-card",
    name: "Aquarium Wavemaker summary",
    description: "What each wavemaker is doing now, what it follows, the next change and a 24 h timeline.",
  },
].forEach((card) => {
  if (!window.customCards.some((c) => c.type === card.type)) window.customCards.push(card);
});
})();
