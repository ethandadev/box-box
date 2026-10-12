// Box Box web UI. Vanilla JS, no build step. Talks to the local engine over REST + one WebSocket.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  status: null,
  settings: null,
  schema: [],
  providers: [],
  models: {},
  devices: { output: [], input: [] },
  page: "dashboard",
  step: "you",
};

// ---------------------------------------------------------------------------------------- API

async function api(path, body) {
  const opts = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = (isJson && (data.error || data.message)) || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

// ---------------------------------------------------------------------------------------- toasts

const popoverSupported = "popover" in HTMLElement.prototype;

function toast(message, kind = "ok", ms = 3200) {
  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.kind = kind;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  el.textContent = message;
  const box = $("#toasts");
  box.append(el);
  const offset = () => $$(".toast", box).forEach((t, i, all) => { t.style.top = `${4.5 + (all.length - 1 - i) * 3.6}rem`; });
  offset();
  if (popoverSupported) {
    el.popover = "manual";
    el.showPopover();
  } else {
    requestAnimationFrame(() => el.classList.add("open"));
  }
  setTimeout(() => {
    if (popoverSupported && el.matches(":popover-open")) el.hidePopover();
    el.classList.remove("open");
    setTimeout(() => { el.remove(); offset(); }, 250);
  }, ms);
}

// ---------------------------------------------------------------------------------------- routing

function route() {
  const page = (location.hash.slice(1) || "dashboard").split("/")[0];
  const known = $$("[data-page]").map((s) => s.dataset.page);
  state.page = known.includes(page) ? page : "dashboard";
  $$("[data-page]").forEach((s) => (s.hidden = s.dataset.page !== state.page));
  $$("[data-nav]").forEach((a) => a.dataset.nav === state.page ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
  if (state.page === "diagnostics") loadDiagnostics();
  if (state.page === "recordings") loadRecordings();
  if (state.page === "settings") renderSettings();
  if (state.page === "setup") showStep(state.step);
}

// ---------------------------------------------------------------------------------------- settings helpers

function getSetting(path) {
  const [section, key] = path.split(".");
  return state.settings?.[section]?.[key];
}

let saveTimer = null;
let pendingPatch = {};

function queueSave(path, value, immediate = false) {
  const [section, key] = path.split(".");
  pendingPatch[section] = { ...(pendingPatch[section] || {}), [key]: value };
  if (path === "ai.provider" && state.settings.ai.provider !== value) {
    state.settings.ai.model = "";
    for (const input of $$('input[name="ai.model"]')) input.value = "";
  }
  state.settings[section][key] = value;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, immediate ? 0 : 350);
}

async function flushSave() {
  const patch = pendingPatch;
  pendingPatch = {};
  if (!Object.keys(patch).length) return;
  try {
    const res = await api("/api/settings", patch);
    state.settings = res.settings;
    toast("Saved");
  } catch (e) {
    toast(`Couldn't save: ${e.message}`, "error");
  }
}

function coerce(input) {
  if (input.type === "checkbox") return input.checked;
  if (input.type === "range" || input.type === "number") return Number(input.value);
  return input.value;
}

function fillDeviceSelect(select, kind) {
  const current = getSetting(select.name) || "";
  select.replaceChildren(new Option("Windows default", ""));
  for (const d of state.devices[kind] || []) {
    select.append(new Option(d.name + (d.default ? " (default)" : ""), d.name));
  }
  if (current && ![...select.options].some((o) => o.value === current)) {
    select.append(new Option(`${current} (not connected)`, current));
  }
  select.value = current;
}

function bindAutosaveForm(form) {
  for (const input of $$("input, select", form)) {
    if (!input.name) continue;
    const value = getSetting(input.name);
    if (input.dataset.device) fillDeviceSelect(input, input.dataset.device);
    else if (input.type === "radio") input.checked = input.value === value;
    else if (input.type === "checkbox") input.checked = !!value;
    else if (input.type === "password") input.value = value || "";
    else if (value !== undefined && input.tagName !== "SELECT") input.value = value;
    else if (input.tagName === "SELECT" && value !== undefined) input.value = value;
    syncOutput(input);
    if (input.dataset.bound) continue;
    input.dataset.bound = "1";
    const evt = input.type === "range" ? "input" : "change";
    input.addEventListener(evt, () => {
      syncOutput(input);
      if (input.type === "password" && input.value.startsWith("••••")) return;
      if (input.required && !input.value.trim()) return;
      queueSave(input.name, coerce(input));
      if (input.name.startsWith("ai.")) refreshAiFields();
    });
  }
  refreshAiFields();
}

// ---------------------------------------------------------------------------------------- AI provider fields

function currentProvider() {
  const id = getSetting("ai.provider");
  return state.providers.find((p) => p.id === id) || state.providers[0];
}

function refreshAiFields() {
  const prov = currentProvider();
  if (!prov || !state.settings) return;
  for (const el of $$("[data-when]")) {
    const cond = JSON.parse(el.dataset.when);
    const section = el.dataset.section;
    el.hidden = !Object.entries(cond).every(([k, vals]) => vals.includes(getSetting(`${section}.${k}`)));
  }
  const fetched = state.models[prov.id] || [];
  const suggestions = [...new Set([...(prov.models || []), ...fetched])];
  for (const pick of $$("select[data-model-pick]")) {
    const current = getSetting("ai.model") || "";
    const opts = [new Option(fetched.length ? `Pick from ${fetched.length} models…` : "Suggested models…", "")];
    for (const m of suggestions) opts.push(new Option(m === prov.default_model ? `${m} (recommended)` : m, m));
    pick.replaceChildren(...opts);
    pick.value = suggestions.includes(current) ? current : "";
    pick.hidden = suggestions.length === 0;
    if (!pick.dataset.bound) {
      pick.dataset.bound = "1";
      pick.addEventListener("change", () => {
        if (!pick.value) return;
        const input = pick.closest(".setting, .field")?.querySelector('input[name="ai.model"]') || $('input[name="ai.model"]');
        for (const i of $$('input[name="ai.model"]')) i.value = pick.value;
        queueSave("ai.model", pick.value, true);
        if (input) input.focus();
      });
    }
  }
  for (const input of $$('input[name="ai.model"]')) {
    input.placeholder = prov.default_model ? `${prov.default_model} (recommended)` : "Leave empty to use the loaded model";
  }
  for (const input of $$('input[name="ai.base_url"]')) input.placeholder = prov.base_url || "http://host:port/v1";
  for (const note of $$("[data-ai-note]")) {
    const key = prov.key_url ? ` Get a key at <a href="${escapeHtml(prov.key_url)}" target="_blank" rel="noopener">${escapeHtml(new URL(prov.key_url).host)}</a>.` : "";
    note.innerHTML = `${escapeHtml(prov.note)}${key}`;
  }
  if (prov.key_field) {
    for (const input of $$(`input[name="ai.${prov.key_field}"]`)) input.placeholder = prov.key_hint || "";
  }
}

async function listModels(button) {
  await flushSave();
  const prov = currentProvider();
  const status = button.parentElement.parentElement.querySelector("[data-models-status]");
  button.disabled = true;
  button.textContent = "Checking…";
  try {
    const res = await api("/api/ai/models", {});
    if (res.ok) state.models[prov.id] = res.models;
    if (status) {
      status.hidden = false;
      status.textContent = res.ok ? `${res.models.length} models available. Pick one from the list below the box.` : res.message;
      status.dataset.state = res.ok ? "ok" : "error";
    }
    refreshAiFields();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "List models";
  }
}

function syncOutput(input) {
  if (input.type !== "range") return;
  const out = input.parentElement.querySelector("output");
  if (out) out.textContent = Number(input.value).toFixed(2).replace(/\.?0+$/, "") || "0";
}

// ---------------------------------------------------------------------------------------- settings page

const SECTION_TITLES = {
  driver: "You",
  engineer: "Engineer",
  voice: "Voice",
  mic: "Microphone & speech",
  ptt: "Push-to-talk",
  telemetry: "Telemetry & recording",
  ai: "AI conversation",
  callouts: "What he tells you",
  ui: "App",
};

function renderSettings() {
  const form = $("#settings-form");
  if (!state.settings || !state.schema.length) return;
  form.replaceChildren();
  const bySection = {};
  for (const f of state.schema) (bySection[f.section] ||= []).push(f);
  for (const [section, fields] of Object.entries(bySection)) {
    const visible = fields.filter((f) => !f.hidden);
    const fs = document.createElement("fieldset");
    fs.className = "card";
    const legend = document.createElement("legend");
    legend.textContent = SECTION_TITLES[section] || section;
    fs.append(legend);
    if (section === "ptt") {
      const p = document.createElement("div");
      p.className = "setting";
      p.innerHTML = `<span>Button: <strong>${escapeHtml(state.status?.ptt?.binding || "not set")}</strong> · <a href="#setup" data-step="ptt">change in Setup</a></span>`;
      fs.append(p);
    }
    for (const f of visible) fs.append(settingField(f));
    form.append(fs);
  }
  bindAutosaveForm(form);
}

function settingField(f, prefix = "set") {
  const id = `${prefix}-${f.section}-${f.key}`;
  const name = `${f.section}.${f.key}`;
  const wrap = document.createElement("div");
  wrap.className = "setting";
  const help = f.help ? `<small id="${id}-help">${escapeHtml(f.help)}</small>` : "";
  const describedBy = f.help ? `aria-describedby="${id}-help"` : "";
  if (f.type === "bool") {
    wrap.innerHTML = `<label class="toggle" for="${id}">${escapeHtml(f.label)}<input type="checkbox" id="${id}" name="${name}" ${describedBy}></label>${help}`;
  } else if (f.device) {
    wrap.innerHTML = `<label for="${id}">${escapeHtml(f.label)}</label><select id="${id}" name="${name}" data-device="${f.device}" ${describedBy}></select>${help}`;
  } else if (f.choices) {
    const opts = f.choices.map(([v, l]) => `<option value="${escapeHtml(v)}">${escapeHtml(l)}</option>`).join("");
    wrap.innerHTML = `<label for="${id}">${escapeHtml(f.label)}</label><select id="${id}" name="${name}" ${describedBy}>${opts}</select>${help}`;
    if (f.section === "ai" && f.key === "provider") wrap.insertAdjacentHTML("beforeend", `<small class="ai-note" data-ai-note></small>`);
  } else if (f.type === "float") {
    wrap.innerHTML = `<label for="${id}">${escapeHtml(f.label)}</label><div class="range-row"><input type="range" id="${id}" name="${name}" min="${f.min}" max="${f.max}" step="${f.step || 0.05}" ${describedBy}><output for="${id}"></output></div>${help}`;
  } else if (f.type === "int") {
    wrap.innerHTML = `<label for="${id}">${escapeHtml(f.label)}</label><input type="number" id="${id}" name="${name}" min="${f.min ?? ""}" max="${f.max ?? ""}" ${describedBy}>${help}`;
  } else if (f.suggest === "models") {
    wrap.innerHTML = `<label for="${id}">${escapeHtml(f.label)}</label><div class="row"><input type="text" id="${id}" name="${name}" autocomplete="off" spellcheck="false" ${describedBy}><button class="btn" type="button" data-list-models>List models</button></div><select data-model-pick aria-label="Pick a model" hidden></select>${help}<small data-models-status hidden></small>`;
  } else if (f.secret) {
    wrap.innerHTML = `<label for="${id}">${escapeHtml(f.label)}</label><div class="row"><input type="password" id="${id}" name="${name}" autocomplete="off" spellcheck="false" ${describedBy}><button class="btn" type="button" data-toggle-secret="${id}">Show</button></div>${help}`;
  } else {
    wrap.innerHTML = `<label for="${id}">${escapeHtml(f.label)}</label><input type="text" id="${id}" name="${name}" ${describedBy}>${help}`;
  }
  if (f.when) {
    wrap.dataset.when = JSON.stringify(f.when);
    wrap.dataset.section = f.section;
  }
  return wrap;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// ---------------------------------------------------------------------------------------- dashboard

const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function addRadio(event) {
  const log = $("#radio-log");
  const li = document.createElement("li");
  li.className = "msg";
  let who = state.settings?.engineer?.name || "Engineer";
  if (event.type === "driver") {
    li.classList.add("driver");
    who = state.settings?.driver?.name || "You";
  } else if (event.type === "queued") {
    li.classList.add("queued");
    li.hidden = !$("#show-queued").checked;
    who = `${who} considered`;
  } else if (event.type === "warning") {
    li.classList.add("system");
    who = "Box Box";
  }
  if (event.priority === "SAFETY") li.classList.add("safety");
  if (event.id) li.dataset.id = event.id;
  li.innerHTML = `<span class="who">${escapeHtml(who)}</span><time>${fmtTime(event.at)}</time>${escapeHtml(event.text || event.message || "")}`;
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  log.append(li);
  while (log.children.length > 300) log.firstElementChild.remove();
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

function setText(sel, text) {
  const el = $(sel);
  if (el && el.textContent !== String(text)) el.textContent = text;
}

function renderStatus(st) {
  state.status = st;
  // pills
  const pill = (id, s) => ($(`#pill-${id}`).dataset.state = s);
  const t = st.telemetry;
  pill("telemetry", t.error ? "error" : t.connected ? "ok" : "warn");
  const v = st.voice;
  pill("voice", v.output_error ? "error" : v.speaking ? "active" : v.engine === v.wanted ? "ok" : v.wanted === "off" ? "off" : "warn");
  pill("mic", !st.mic.ok ? "error" : st.mic.recording ? "active" : st.stt.ready ? "ok" : "warn");
  pill("ptt", st.ptt.active ? "active" : st.ptt.kind === "none" ? "warn" : "ok");
  pill("ai", st.ai.error ? "error" : !st.ai.enabled ? "off" : !st.ai.available ? "warn" : st.ai.status === "ready" ? "ok" : "warn");

  $("#setup-banner").hidden = st.onboarded || state.page === "setup";
  $("#replay-banner").hidden = !t.replaying;
  if (t.replaying) setText("#replay-name", t.replaying);

  // session
  const s = st.session;
  $("#no-telemetry").hidden = !!s;
  if (s) {
    setText("#s-session", s.session);
    setText("#s-track", s.track);
    setText("#s-weather", s.weather);
    setText("#s-temp", `${s.track_temp}°C`);
    if (s.is_race) {
      setText("#s-remaining-label", "Laps");
      setText("#s-remaining", st.player ? `${st.player.lap}/${s.total_laps}` : s.total_laps);
    } else {
      setText("#s-remaining-label", "Time left");
      setText("#s-remaining", s.time_left != null ? `${Math.floor(s.time_left / 60)}:${String(s.time_left % 60).padStart(2, "0")}` : "–");
    }
    const sc = { 1: "SAFETY CAR", 2: "VIRTUAL SAFETY CAR", 3: "FORMATION LAP" }[s.safety_car];
    $("#sc-banner").hidden = !sc;
    if (sc) setText("#sc-banner", sc);
  }
  // car
  const p = st.player;
  if (p) {
    setText("#c-pos", `P${p.position}`);
    setText("#c-lap", p.lap);
    setText("#c-status", p.in_pits ? "Pit lane" : p.status);
    setText("#c-speed", `${p.speed} km/h`);
    setText("#c-compound", p.compound ? `${p.compound} · ${p.tyre_age} laps` : "–");
    setText("#c-best", p.best_lap || "–");
    setText("#c-last", p.last_lap || "–");
    setText("#c-fuel", p.fuel_laps != null ? `${p.fuel_laps > 0 ? "+" : ""}${p.fuel_laps} laps` : "–");
    $("#c-battery").value = p.battery ?? 0;
    if (p.fastest !== null && p.fastest !== undefined) {
      // Practice/quali: who's quickest, and how far off we are.
      setText("#g-ahead-label", "Fastest");
      setText("#g-ahead-name", p.fastest.name);
      setText("#g-ahead", p.fastest.time);
      setText("#g-behind-label", "Your gap");
      setText("#g-behind-name", p.fastest.is_me ? "You're quickest" : "to P1");
      setText("#g-behind", p.fastest.is_me || p.fastest.delta == null ? "" : `+${p.fastest.delta.toFixed(3)}`);
    } else {
      setText("#g-ahead-label", "Ahead");
      setText("#g-behind-label", "Behind");
      setText("#g-ahead-name", p.ahead?.name || "–");
      setText("#g-ahead", p.ahead ? `+${p.ahead.gap.toFixed(1)}` : "");
      setText("#g-behind-name", p.behind?.name || "–");
      setText("#g-behind", p.behind ? `-${p.behind.gap.toFixed(1)}` : "");
    }
    for (let i = 0; i < 4; i++) {
      const el = $(`#t-${i}`);
      const wear = p.wear?.[i];
      const temp = p.tyre_temps?.[i];
      setText(`#t-${i} .w`, wear != null ? `${wear}%` : "–");
      setText(`#t-${i} .t`, temp != null ? `${temp}°` : "");
      el.dataset.temp = temp == null ? "" : temp < 75 ? "cold" : temp > 112 ? "hot" : "ok";
    }
  }
  // why he's quiet (calls waiting and what's holding them)
  const why = $("#why-quiet");
  if (why) {
    why.hidden = !st.radio.why_quiet;
    setText("#why-quiet", st.radio.why_quiet ? `Holding ${st.radio.pending.length} message(s): ${st.radio.why_quiet}` : "");
  }
  // verbosity
  const verbosity = st.radio.verbosity;
  for (const r of $$('input[name="verbosity"]')) r.checked = r.value === verbosity;
  const muted = st.radio.muted_for;
  setText("#mute-btn", muted > 0 ? `Radio silence: ${Math.ceil(muted / 60)} min (end)` : "Radio silence 5 min");
  $("#talk-btn").setAttribute("aria-pressed", st.ptt.active || st.mic.recording ? "true" : "false");
  // speaking highlight
  $$(".msg.speaking").forEach((m) => m.classList.remove("speaking"));

  if (state.page === "setup") updateWizardLive(st);
}

// ---------------------------------------------------------------------------------------- websocket

let ws = null;
let wsBackoff = 500;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => {
    wsBackoff = 500;
    $("#conn-lost").hidden = true;
  };
  ws.onmessage = (msg) => {
    let event;
    try { event = JSON.parse(msg.data); } catch { return; }
    switch (event.type) {
      case "status": renderStatus(event.status); break;
      case "radio": addRadio(event); break;
      case "driver": addRadio(event); break;
      case "queued": addRadio(event); break;
      case "warning":
        addRadio(event);
        if (!event.replayed) toast(event.message, "warn", 6000);
        break;
      case "settings": state.settings = event.settings; break;
      case "replay":
        if (!event.replayed && event.state === "finished") toast(`Finished replaying ${event.name}`);
        break;
      case "transcript":
        if (!event.replayed && !event.text) toast("Didn't catch that. Try again, a bit closer to the mic.", "warn");
        break;
    }
  };
  ws.onclose = () => {
    $("#conn-lost").hidden = false;
    setTimeout(connect, wsBackoff);
    wsBackoff = Math.min(wsBackoff * 2, 5000);
  };
}

// ---------------------------------------------------------------------------------------- talking

let talking = false;
async function setTalk(down) {
  if (down === talking) return;
  talking = down;
  $("#talk-btn").setAttribute("aria-pressed", down ? "true" : "false");
  try { await api("/api/ptt/ui", { down }); } catch (e) { toast(e.message, "error"); }
}

function bindTalk() {
  const btn = $("#talk-btn");
  btn.addEventListener("pointerdown", (e) => { e.preventDefault(); btn.setPointerCapture(e.pointerId); setTalk(true); });
  btn.addEventListener("pointerup", () => setTalk(false));
  btn.addEventListener("pointercancel", () => setTalk(false));
  btn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); setTalk(true); } });
  btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") setTalk(false); });
  // Space anywhere on the dashboard (not while typing) acts as push-to-talk.
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !e.repeat && state.page === "dashboard" && !e.target.closest("input, select, textarea, button")) {
      e.preventDefault();
      setTalk(true);
    }
  });
  document.addEventListener("keyup", (e) => {
    if (e.code === "Space" && talking && !e.target.closest("input, select, textarea")) setTalk(false);
  });
  window.addEventListener("blur", () => setTalk(false));

  $("#ask-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#ask-text");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    try { await api("/api/ask", { text }); } catch (err) { toast(err.message, "error"); }
  });

  for (const r of $$('input[name="verbosity"]')) {
    r.addEventListener("change", () => queueSave("engineer.verbosity", r.value, true));
  }
  $("#show-queued").addEventListener("change", (e) => {
    $$(".msg.queued").forEach((m) => (m.hidden = !e.target.checked));
  });
  $("#mute-btn").addEventListener("click", () => {
    const muted = state.status?.radio?.muted_for > 0;
    api("/api/ask", { text: muted ? "you can talk again" : "radio silence for 5 minutes" }).catch((e) => toast(e.message, "error"));
  });
}

// ---------------------------------------------------------------------------------------- setup wizard

const STEPS = ["you", "voice", "game", "mic", "ptt", "ai", "done"];

function showStep(step) {
  state.step = STEPS.includes(step) ? step : "you";
  $$(".step").forEach((s) => (s.hidden = s.dataset.step !== state.step));
  const idx = STEPS.indexOf(state.step);
  $$("#steps li").forEach((li, i) => {
    li.toggleAttribute("data-done", i < idx);
    li.dataset.step === state.step ? li.setAttribute("aria-current", "step") : li.removeAttribute("aria-current");
  });
  const card = $(`.step[data-step="${state.step}"]`);
  for (const form of $$("form[data-autosave]", card)) bindAutosaveForm(form);
  if (state.step === "voice") {
    const sel = $("#w-voice");
    if (!sel.options.length) {
      const field = state.schema.find((f) => f.section === "voice" && f.key === "voice");
      for (const [v, l] of field?.choices || []) sel.append(new Option(l, v));
    }
    sel.value = getSetting("voice.voice");
  }
  if (state.step === "game") setText("#w-port", getSetting("telemetry.port"));
  if (state.step === "ai") renderWizardAi();
  if (state.step === "done") renderDoneSummary();
  if (state.status) updateWizardLive(state.status);
}

const WIZARD_AI_FIELDS = ["provider", "model", "api_key", "openai_key", "gemini_key", "openrouter_key", "poe_key", "custom_key", "base_url"];

function renderWizardAi() {
  const form = $("#w-ai-form");
  if (form.dataset.rendered) return refreshAiFields();
  form.dataset.rendered = "1";
  const fields = state.schema.filter((f) => f.section === "ai" && WIZARD_AI_FIELDS.includes(f.key));
  form.replaceChildren(...fields.map((f) => settingField(f, "w")));
  bindAutosaveForm(form);
}

function updateWizardLive(st) {
  if (state.step === "voice") {
    const m = st.voice.model;
    const show = st.voice.wanted === "kokoro" && m.state !== "ready";
    $("#voice-download").hidden = !show;
    if (show) {
      $("#voice-progress").value = m.percent;
      setText("#voice-download-text", m.state === "error"
        ? `Voice download failed: ${m.error}. Using the Windows voice for now.`
        : `Downloading the voice (about 120 MB, first time only)… ${m.percent}%`);
    }
  }
  if (state.step === "game") {
    const el = $("#game-detect");
    const t = st.telemetry;
    if (t.error) {
      el.dataset.state = "error";
      el.textContent = t.error;
    } else if (t.connected && st.session) {
      el.dataset.state = "ok";
      el.textContent = `Connected: ${st.session.track} ${st.session.session}, format ${t.format}${t.format === 2025 ? " (set UDP Format to 2026 for the 2026 data)" : ""}.`;
    } else if (t.warning) {
      el.dataset.state = "error";
      el.textContent = t.warning;
    } else {
      el.dataset.state = "wait";
      el.textContent = "Waiting for the game… start a session (time trial is fine).";
    }
  }
  if (state.step === "mic") {
    $("#mic-level").value = Math.min(1, st.mic.level * 8);
    setText("#stt-status", st.mic.error ? st.mic.error : `Speech recognition: ${st.stt.status}`);
  }
  if (state.step === "ptt") {
    setText("#ptt-binding", st.ptt.binding);
  }
}

function renderDoneSummary() {
  const st = state.status;
  const items = [
    [st.telemetry.connected ? "ok" : "warn", st.telemetry.connected ? "Game connected" : "Game not seen yet (start a session when you're ready)"],
    [st.voice.engine === st.voice.wanted ? "ok" : "warn", `Voice: ${st.voice.status}`],
    [st.mic.ok ? "ok" : "error", st.mic.ok ? "Microphone working" : `Microphone: ${st.mic.error}`],
    [st.ptt.kind !== "none" ? "ok" : "warn", `Push-to-talk: ${st.ptt.binding}`],
    [st.ai.available ? "ok" : "warn", st.ai.available ? `AI: ${st.ai.provider_label} (${st.ai.model})` : `AI off: common questions only${st.ai.problem ? ` (${st.ai.problem})` : ""}`],
  ];
  $("#done-summary").replaceChildren(...items.map(([s, text]) => {
    const li = document.createElement("li");
    li.dataset.state = s;
    li.textContent = text;
    return li;
  }));
}

function bindWizard() {
  $$("#steps li").forEach((li) => li.addEventListener("click", () => showStep(li.dataset.step)));
  document.addEventListener("click", (e) => {
    const next = e.target.closest("[data-next]");
    const prev = e.target.closest("[data-prev]");
    if (next || prev) {
      e.preventDefault();
      flushSave();
      const i = STEPS.indexOf(state.step) + (next ? 1 : -1);
      showStep(STEPS[Math.max(0, Math.min(STEPS.length - 1, i))]);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
    const secret = e.target.closest("[data-toggle-secret]");
    if (secret) {
      const input = document.getElementById(secret.dataset.toggleSecret);
      input.type = input.type === "password" ? "text" : "password";
      secret.textContent = input.type === "password" ? "Show" : "Hide";
    }
    const open = e.target.closest("[data-open]");
    if (open) api("/api/open", { which: open.dataset.open }).then((r) => toast(`Opened ${r.path}`)).catch((err) => toast(err.message, "error"));
    const lm = e.target.closest("[data-list-models]");
    if (lm) listModels(lm);
    const step = e.target.closest("a[data-step]");
    if (step) state.step = step.dataset.step;
  });
  $("[data-test-voice]").addEventListener("click", async () => {
    await flushSave();
    try { await api("/api/voice/test", {}); } catch (e) { toast(e.message, "error"); }
  });

  const dialog = $("#learn-dialog");
  if (!("closedBy" in HTMLDialogElement.prototype)) {
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const r = dialog.getBoundingClientRect();
      const inside = r.top <= event.clientY && event.clientY <= r.bottom && r.left <= event.clientX && event.clientX <= r.right;
      if (!inside) dialog.close();
    });
  }
  $("#learn-btn").addEventListener("click", async () => {
    const status = $("#learn-status");
    dialog.showModal();
    const progress = $("#learn-progress");
    progress.value = 0;
    const timer = setInterval(() => (progress.value = Math.min(15, progress.value + 0.25)), 250);
    try {
      const res = await api("/api/ptt/learn", {});
      status.hidden = false;
      if (res.ok) {
        status.dataset.state = "ok";
        status.textContent = `Bound to ${res.binding}. Hold it and say “radio check” to test.`;
        toast(`Push-to-talk: ${res.binding}`);
      } else {
        status.dataset.state = "error";
        status.textContent = res.error;
      }
    } catch (e) {
      status.hidden = false;
      status.dataset.state = "error";
      status.textContent = e.message;
    } finally {
      clearInterval(timer);
      if (dialog.open) dialog.close();
    }
  });
  $("#clear-btn").addEventListener("click", () => api("/api/ptt/clear", {}).then(() => toast("Push-to-talk cleared")));
  $("#udp-btn").addEventListener("click", () => api("/api/ptt/udp", { action: 1 }).then(() => toast("Using the game's UDP Action 1 (toggle mode)")));
  $("#ai-test").addEventListener("click", async () => {
    await flushSave();
    const el = $("#ai-status");
    el.hidden = false;
    el.dataset.state = "busy";
    el.textContent = "Testing…";
    try {
      const res = await api("/api/ai/test", {});
      el.dataset.state = res.ok ? "ok" : "error";
      el.textContent = res.message;
    } catch (e) {
      el.dataset.state = "error";
      el.textContent = e.message;
    }
  });
  $("#finish-btn").addEventListener("click", async () => {
    await flushSave();
    await api("/api/settings", { onboarded: true });
    state.settings.onboarded = true;
    location.hash = "#dashboard";
    toast("All set. Go drive!");
  });
}

// ---------------------------------------------------------------------------------------- diagnostics

async function loadDiagnostics() {
  try {
    const { checks } = await api("/api/diagnostics");
    $("#checks").replaceChildren(...checks.map((c) => {
      const li = document.createElement("li");
      li.dataset.state = c.status;
      li.innerHTML = `<span class="status">${c.status.toUpperCase()}</span>
        <div><strong>${escapeHtml(c.label)}</strong><div>${escapeHtml(c.detail)}</div>${c.fix && c.status !== "ok" ? `<div class="fix">${escapeHtml(c.fix)}</div>` : ""}</div>
        ${c.action ? `<button class="btn small" type="button" data-fix="${c.action}">Fix</button>` : "<span></span>"}`;
      return li;
    }));
    const { lines } = await api("/api/logs");
    $("#log").textContent = lines.map((l) => `${fmtTime(l.t)} ${l.level.padEnd(7)} ${l.name}: ${l.msg}`).join("\n");
  } catch (e) {
    toast(`Diagnostics failed: ${e.message}`, "error");
  }
}

function bindDiagnostics() {
  $("#checks").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-fix]");
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = "Working…";
    try {
      const res = await api("/api/fix", { action: btn.dataset.fix });
      toast(res.message || (res.ok ? "Done" : "Didn't work"), res.ok ? "ok" : "error", 6000);
    } catch (err) {
      toast(err.message, "error");
    }
    loadDiagnostics();
  });
  $("#refresh-diag").addEventListener("click", loadDiagnostics);
  $("#copy-report").addEventListener("click", async () => {
    try {
      const text = await api("/api/report");
      await navigator.clipboard.writeText(text);
      toast("Support report copied. Paste it wherever you're asking for help.");
    } catch (e) {
      toast(`Couldn't copy: ${e.message}`, "error");
    }
  });
}

// ---------------------------------------------------------------------------------------- recordings

async function loadRecordings() {
  try {
    const data = await api("/api/recordings");
    setText("#rec-folder", data.folder);
    const tbody = $("#rec-table tbody");
    tbody.replaceChildren(...data.files.map((f) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${escapeHtml(f.name.replace(/\.f1raw(\.gz)?$/, ""))}</td><td>${f.size_mb} MB</td>
        <td>${new Date(f.modified * 1000).toLocaleString()}</td>
        <td><button class="btn small" data-replay="${escapeHtml(f.name)}" data-speed="1">▶ Play</button><button class="btn small" data-replay="${escapeHtml(f.name)}" data-speed="4">4×</button></td>`;
      return tr;
    }));
    $("#rec-empty").hidden = data.files.length > 0;
    const hist = $("#hist-table tbody");
    hist.replaceChildren(...data.sessions.map((s) => {
      const tr = document.createElement("tr");
      const best = s.best_lap_ms ? `${Math.floor(s.best_lap_ms / 60000)}:${((s.best_lap_ms % 60000) / 1000).toFixed(3).padStart(6, "0")}` : "–";
      tr.innerHTML = `<td>${new Date(s.started_at * 1000).toLocaleString()}</td><td>${escapeHtml(s.track)}</td><td>${escapeHtml(s.session_type)}</td><td class="num">${best}</td><td>${s.position ? `P${s.position}` : "–"}</td>`;
      return tr;
    }));
  } catch (e) {
    toast(`Couldn't load recordings: ${e.message}`, "error");
  }
}

function bindRecordings() {
  $("#rec-table").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-replay]");
    if (!btn) return;
    try {
      await api("/api/replay", { name: btn.dataset.replay, speed: Number(btn.dataset.speed) });
      toast(`Replaying ${btn.dataset.replay}. Watch the dashboard.`);
      location.hash = "#dashboard";
    } catch (err) {
      toast(err.message, "error");
    }
  });
  $("#replay-stop").addEventListener("click", () => api("/api/replay/stop", {}).catch((e) => toast(e.message, "error")));
}

// ---------------------------------------------------------------------------------------- boot

async function boot() {
  try {
    const data = await api("/api/state");
    state.settings = data.settings;
    state.schema = data.schema;
    state.providers = data.providers || [];
    state.devices = data.devices;
    renderStatus(data.status);
    if (!data.status.onboarded && !location.hash) location.hash = "#setup";
  } catch (e) {
    toast(`Can't reach Box Box: ${e.message}`, "error", 10000);
  }
  bindTalk();
  bindWizard();
  bindDiagnostics();
  bindRecordings();
  window.addEventListener("hashchange", route);
  route();
  connect();
  // Device lists change when headsets are plugged in; refresh them now and then.
  setInterval(async () => {
    try { state.devices = await api("/api/devices"); } catch { /* engine restarting */ }
  }, 15000);
}

boot();
