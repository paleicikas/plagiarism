"use strict";
/* Plagiarism desktop UI. Talks to Python through window.pywebview.api (see app/api.py);
   progress events arrive through window.__plagcheckEvents (see app/runner.py). */

const S = {
  boot: null, settings: null, L: {}, lang: "en", projects: [], screen: "", pid: null, project: null,
  run: null, log: [], logPid: null, logLevel: "all", logQuery: "", autoscroll: true, estimate: null,
  estimating: false, wizardStep: 0, keyStatus: {}, models: {}, reportLang: null,
};
const KEY_URLS = {
  serper: "https://serper.dev/api-key", tavily: "https://app.tavily.com/home",
  anthropic: "https://console.anthropic.com/settings/keys", openai: "https://platform.openai.com/api-keys",
};
const SITE = "https://paleicikas.github.io/plagiarism/";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
/* Material-style inline icons (no icon font, so the app works offline) */
const IC = {
  upload: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M19.35 10.04A7.49 7.49 0 0 0 12 4C9.11 4 6.6 5.64 5.35 8.04A5.994 5.994 0 0 0 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM14 13v4h-4v-4H7l5-5 5 5h-3z"/></svg>',
  check: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>',
  close: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>',
  back: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>',
  chev: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/></svg>',
  refresh: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>',
  spinner: '<span class="spinner" aria-hidden="true"></span>',
  lock: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zM9 8V6c0-1.66 1.34-3 3-3s3 1.34 3 3v2H9z"/></svg>',
};

/* ------------------------------------------------------------------ i18n */
function look(d, k) { let c = d; for (const p of k.split(".")) { if (c == null || typeof c !== "object") return null; c = c[p]; } return typeof c === "string" ? c : null; }
function t(k, p) {
  let s = look(S.L[S.lang] || {}, k) ?? look(S.L.en || {}, k) ?? k;
  if (p) for (const [a, b] of Object.entries(p)) s = s.split("{" + a + "}").join(b);
  return s;
}
const locale = () => ((S.L[S.lang] || {})._meta || {}).locale || S.lang;
const fmt = (n, d = 0) => n == null || isNaN(n) ? "–" : Number(n).toLocaleString(locale(), { maximumFractionDigits: d, minimumFractionDigits: d });
const usd = (n) => n == null ? "–" : "$" + Number(n).toLocaleString(locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function dur(sec) {
  if (sec == null || isNaN(sec)) return "–";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  if (h) return `${h} ${t("app.unit.h")} ${String(m).padStart(2, "0")} ${t("app.unit.min")}`;
  if (m) return `${m} ${t("app.unit.min")} ${String(s).padStart(2, "0")} ${t("app.unit.s")}`;
  return `${s} ${t("app.unit.s")}`;
}
function durMin(min) {
  if (min == null || isNaN(min)) return "–";
  min = Math.max(1, Math.round(min));
  return min < 60 ? `${min} ${t("app.unit.min")}` : `${Math.floor(min / 60)} ${t("app.unit.h")} ${min % 60} ${t("app.unit.min")}`;
}
function when(iso) { if (!iso) return ""; const d = new Date(iso); return d.toLocaleString(locale(), { dateStyle: "medium", timeStyle: "short" }); }
function errText(e) {
  if (!e) return "";
  const k = "error." + e.code; const s = t(k, e.params || {});
  return s === k ? (e.message || e.code) : s;
}
function applyStatic(root = document) {
  $$("[data-t]", root).forEach(el => { el.textContent = t(el.dataset.t); });
  if (root === document) document.title = t("app.name");
}

/* ------------------------------------------------------------------ bridge */
async function call(name, ...args) {
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api[name]) throw { code: "unexpected", params: { detail: "bridge not ready: " + name } };
  const r = await api[name](...args);
  if (r && r.error) throw r.error;
  return r;
}
function fail(e) { console.error(e); toast(errText(e) || String(e), { bad: true }); }

let toastTimer;
function toast(msg, opt = {}) {
  const el = $("#toast");
  el.innerHTML = esc(msg) + (opt.action ? ` <button class="btn small" style="margin-left:10px">${esc(opt.action.label)}</button>` : "");
  el.className = "on" + (opt.bad ? " bad" : "");
  el.style.pointerEvents = opt.action ? "auto" : "none";
  if (opt.action) $("button", el).onclick = () => { opt.action.run(); el.className = ""; };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ""; el.style.pointerEvents = "none"; }, opt.ms || (opt.action ? 7000 : 3500));
}
function modal(html, buttons) {
  return new Promise(res => {
    $("#modal-body").innerHTML = html;
    $("#modal-actions").innerHTML = "";
    for (const b of buttons) {
      const el = document.createElement("button");
      el.className = "btn " + (b.cls || ""); el.textContent = b.label;
      el.onclick = () => { $("#modal").hidden = true; res(b.value); };
      $("#modal-actions").appendChild(el);
    }
    $("#modal").hidden = false;
  });
}
const confirmBox = (title, text, ok, cls = "primary") => modal(`<h2>${esc(title)}</h2><p class="muted">${esc(text)}</p>`,
  [{ label: t("app.common.cancel"), value: false }, { label: ok, value: true, cls }]);

/* ------------------------------------------------------------------ boot */
async function boot() {
  try {
    S.boot = await call("init");
  } catch (e) { $("#main").innerHTML = `<div class="card"><h2>Error</h2><pre>${esc(JSON.stringify(e, null, 1))}</pre></div>`; return; }
  S.L = S.boot.locales; S.settings = S.boot.settings; S.projects = S.boot.projects;
  S.lang = S.settings.effective_language;
  $("#version").textContent = "v" + S.boot.version + ", " + S.boot.author;
  fillLangSelect();
  $("#ui-lang").addEventListener("change", async e => { await saveSettings({ language: e.target.value }); });
  $$("nav button").forEach(b => b.addEventListener("click", () => {
    const n = b.dataset.nav;
    if (n === "active") return go("progress", S.run && S.run.pid);
    go(n);
  }));
  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop", e => e.preventDefault());
  document.addEventListener("click", e => {
    const a = e.target.closest("a[href^='http']");
    if (a) { e.preventDefault(); call("open_url", a.href).catch(fail); }
    if (!e.target.closest(".menu")) $$(".menu-list").forEach(m => m.hidden = true);
  });
  if (S.boot.active) { const st = await call("run_state", S.boot.active, true); onRunState(st); }
  if (!S.settings.first_run_done) go("wizard");
  else if (S.boot.active) go("progress", S.boot.active);
  else go("new");
}
function fillLangSelect() {
  document.documentElement.lang = S.lang;
  $("#ui-lang").innerHTML = S.boot.languages.map(l => `<option value="${l.code}">${esc(l.name)}</option>`).join("");
  $("#ui-lang").value = S.lang;
  applyStatic();
}
async function saveSettings(values) {
  try {
    S.settings = await call("save_settings", values);
    if (S.lang !== S.settings.effective_language) { S.lang = S.settings.effective_language; fillLangSelect(); render(); }
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------------ routing */
function go(screen, pid) {
  S.screen = screen;
  if (pid !== undefined) S.pid = pid;
  // "New check" and its follow-up screens belong to the Checks tab, so that tab stays marked and leads back
  const section = { new: "history", setup: "history", report: "history", progress: "active" }[screen] || screen;
  $$("nav button").forEach(b => b.classList.toggle("on", b.dataset.nav === section));
  $("#main").classList.toggle("full", screen === "report");
  render();
  const m = $("#main"); m.scrollTop = 0;
  m.classList.remove("enter"); void m.offsetWidth; m.classList.add("enter");
  clearTimeout(go._t); go._t = setTimeout(() => m.classList.remove("enter"), 450);
}
function render() {
  const f = { wizard: renderWizard, new: renderNew, setup: renderSetup, progress: renderProgress, report: renderReport,
    history: renderHistory, settings: renderSettings, about: renderAbout }[S.screen] || renderNew;
  f();
  updateNavActive();
}
function updateNavActive() {
  const active = S.run && ["running", "paused", "starting"].includes(S.run.status);
  $("#nav-active").hidden = !active;
}
async function openProject(pid) {
  try {
    const p = await call("get_project", pid);
    S.project = p;
    if (p.active) return go("progress", pid);
    if (p.status === "done" && p.has_report) return go("report", pid);
    if (["new", "ready"].includes(p.status)) return go("setup", pid);
    go("progress", pid);
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------------ key rows (wizard + settings) */
function keyRow(name) {
  const saved = S.settings.keys[name];
  const st = S.keyStatus[name];
  const stHtml = st ? `<span class="key-status ${st.ok ? "ok" : "bad"}">${esc(st.text)}</span>`
    : `<span class="key-status ${saved ? "ok" : ""}">${saved ? "✓ " + t("app.keys.saved") : t("app.keys.not_set")}</span>`;
  return `<div class="key" data-key="${name}">
    <div class="row"><h3 class="grow" style="margin:0">${esc(t(`app.keys.${name}.name`))}</h3>${stHtml}</div>
    <div class="hint" style="margin:2px 0 8px">${esc(t(`app.keys.${name}.desc`))} <a href="${KEY_URLS[name]}">${esc(t("app.keys.get"))}</a></div>
    <div class="row"><input type="password" autocomplete="off" spellcheck="false" placeholder="${esc(saved ? t("app.keys.placeholder_saved") : t("app.keys.placeholder"))}">
      <button class="btn" data-act="save">${esc(t("app.keys.save_test"))}</button>
      ${saved ? `<button class="btn" data-act="test">${esc(t("app.keys.test"))}</button><button class="btn danger" data-act="remove">${esc(t("app.keys.remove"))}</button>` : ""}
    </div>${["anthropic", "openai"].includes(name) ? `<div class="key-safe">${IC.lock}<span>${esc(t("app.keys.safe"))}</span></div>` : ""}</div>`;
}
function bindKeyRows(root, after) {
  $$(".key", root).forEach(row => {
    const name = row.dataset.key, inp = $("input", row);
    const runTest = async () => {
      S.keyStatus[name] = { ok: true, text: "… " + t("app.keys.testing") }; after();
      try {
        const r = await call("test_key", name, "");
        S.keyStatus[name] = { ok: r.ok, text: (r.ok ? "✓ " : "✕ ") + t("app.keys.result." + r.code) + (r.detail && !r.ok ? ` (${r.detail.slice(0, 120)})` : "") };
        if (r.models) S.models[name] = r.models;
      } catch (e) { S.keyStatus[name] = { ok: false, text: errText(e) }; }
      after();
    };
    $$("button", row).forEach(b => b.addEventListener("click", async () => {
      try {
        if (b.dataset.act === "save") {
          if (!inp.value.trim()) return inp.focus();
          S.settings = await call("set_key", name, inp.value.trim());
          inp.value = "";
          await runTest();
        } else if (b.dataset.act === "test") await runTest();
        else if (b.dataset.act === "remove") {
          S.settings = await call("set_key", name, ""); delete S.keyStatus[name]; after();
        }
      } catch (e) { fail(e); }
    }));
    inp.addEventListener("keydown", e => { if (e.key === "Enter") $("[data-act=save]", row).click(); });
  });
}

/* ------------------------------------------------------------------ wizard */
function renderWizard() {
  const n = S.wizardStep, total = 4;
  const dots = Array.from({ length: total }, (_, i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("");
  let body = "";
  if (n === 0) {
    body = `<h1>${esc(t("app.wizard.welcome_title"))}</h1><p class="lead">${esc(t("app.wizard.welcome_text"))}</p>
      <div class="card"><h3>${esc(t("app.wizard.how_title"))}</h3><ol class="muted" style="margin:6px 0 0 18px;padding:0">
        <li>${esc(t("app.wizard.how1"))}</li><li>${esc(t("app.wizard.how2"))}</li><li>${esc(t("app.wizard.how3"))}</li><li>${esc(t("app.wizard.how4"))}</li></ol></div>
      <div class="card"><h3>${esc(t("app.language"))}</h3><div class="choice">${S.boot.languages.map(l =>
        `<button data-lang="${l.code}" class="${l.code === S.lang ? "on" : ""}"><b>${esc(l.name)}</b><span class="muted small">${esc(l.english_name)}</span></button>`).join("")}</div></div>`;
  } else if (n === 1) {
    body = `<h1>${esc(t("app.wizard.search_title"))}</h1><p class="lead">${esc(t("app.wizard.search_text"))}</p>
      <div class="card">${keyRow("serper")}${keyRow("tavily")}
      <label class="check"><input type="checkbox" id="w-free" ${S.settings.allow_free_search ? "checked" : ""}><span>${esc(t("app.settings.allow_free"))}<div class="hint">${esc(t("app.settings.allow_free_hint"))}</div></span></label></div>`;
  } else if (n === 2) {
    body = `<h1>${esc(t("app.wizard.llm_title"))}</h1><p class="lead">${esc(t("app.wizard.llm_text"))}</p>
      <div class="card">${keyRow("anthropic")}${keyRow("openai")}</div>
      <p class="hint">${esc(t("app.wizard.llm_none"))}</p>`;
  } else {
    body = `<h1>${esc(t("app.wizard.done_title"))}</h1><p class="lead">${esc(t("app.wizard.done_text"))}</p>
      <div class="card"><h3>${esc(t("app.settings.unpaywall"))}</h3><div class="hint">${esc(t("app.settings.unpaywall_hint"))}</div>
      <input type="email" id="w-email" placeholder="name@example.com" value="${esc(S.settings.unpaywall_email || "")}" style="margin-top:8px"></div>
      <div class="card">${summaryOfSetup()}</div>`;
  }
  const next = n < total - 1 ? `<button class="btn primary big" id="w-next">${esc(t(n === 0 ? "app.wizard.start" : "app.common.next"))}</button>`
    : `<button class="btn primary big" id="w-finish">${esc(t("app.wizard.finish"))}</button>`;
  $("#main").innerHTML = `<div class="wizard"><div class="steps-dots">${dots}</div>${body}
    <div class="row" style="margin-top:20px">${n > 0 ? `<button class="btn" id="w-back">${esc(t("app.common.back"))}</button>` : ""}
      <span class="grow"></span>${n === 0 ? `<button class="btn" id="w-demo">${esc(t("app.wizard.try_demo"))}</button>` : ""}${next}</div></div>`;
  bindKeyRows($("#main"), renderWizard);
  $$("[data-lang]").forEach(b => b.onclick = () => saveSettings({ language: b.dataset.lang }));
  const free = $("#w-free"); if (free) free.onchange = () => saveSettings({ allow_free_search: free.checked });
  const email = $("#w-email"); if (email) email.onchange = () => saveSettings({ unpaywall_email: email.value.trim() });
  if ($("#w-back")) $("#w-back").onclick = async () => { await savePendingKeys(); S.wizardStep--; renderWizard(); };
  if ($("#w-next")) $("#w-next").onclick = async () => { await savePendingKeys(); S.wizardStep++; renderWizard(); };
  if ($("#w-demo")) $("#w-demo").onclick = async () => { await savePendingKeys(); await saveSettings({ first_run_done: true }); startDemo(); };
  if ($("#w-finish")) $("#w-finish").onclick = async () => {
    await savePendingKeys();
    if (email) await saveSettings({ unpaywall_email: email.value.trim() });
    await saveSettings({ first_run_done: true }); go("new");
  };
}
/* Keys typed into the setup steps but not yet saved with "Save" are stored before the wizard moves on. */
async function savePendingKeys() {
  for (const row of $$("#main .key")) {
    const inp = $("input", row), value = inp.value.trim();
    if (!value) continue;
    try {
      S.settings = await call("set_key", row.dataset.key, value);
      inp.value = "";
    } catch (e) { fail(e); }
  }
}
function summaryOfSetup(demo) {
  if (demo) return `<p class="small muted" style="margin:0">${esc(t("app.setup.demo_services"))}</p>`;
  const chain = S.settings.search_chain, llm = S.settings.effective_llm;
  const search = chain.length ? chain.map(n => t("app.engine." + n)).join(" → ") : t("app.setup.no_search");
  const ai = llm.provider === "none" ? t("app.setup.no_llm") : `${t("app.engine." + llm.provider)} · ${llm.model}`;
  return `<dl class="kv"><dt>${esc(t("app.setup.search_engines"))}</dt><dd>${esc(search)}</dd>
    <dt>${esc(t("app.setup.ai_model"))}</dt><dd>${esc(ai)}</dd></dl>`;
}

/* ------------------------------------------------------------------ new check */
function renderNew() {
  const recent = S.projects.slice(0, 5);
  $("#main").innerHTML = `<h1>${esc(t("app.new.title"))}</h1><p class="lead">${esc(t("app.new.lead"))}</p>
    ${recommendCard()}
    <div class="drop" id="drop" tabindex="0"><div class="big-ico">${IC.upload}</div><h2>${esc(t("app.new.drop"))}</h2>
      <div class="muted">${esc(t("app.new.drop_hint"))}</div></div>
    <div class="row" style="margin-top:10px"><span class="muted small">${esc(t("app.new.privacy"))}</span><span class="grow"></span>
      <button class="btn link" id="demo">${esc(t("app.new.demo"))}</button></div>
    ${recent.length ? `<div class="spacer"></div><h2>${esc(t("app.new.recent"))}</h2><div class="card" style="padding:4px 10px">${historyTable(recent)}</div>` : ""}`;
  const drop = $("#drop");
  drop.onclick = () => withBusy(drop, () => call("pick_pdf")).then(p => p && !p.cancelled && created(p)).catch(fail);
  drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") drop.click(); };
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = async e => {
    e.preventDefault(); drop.classList.remove("over");
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (!/\.pdf$/i.test(f.name)) return toast(t("error.pdf.not_pdf"), { bad: true });
    try {
      const p = await withBusy(drop, async () => {
        if (f.pywebviewFullPath) return call("create_from_path", f.pywebviewFullPath);
        const data = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
        return call("create_from_data", f.name, data);
      });
      created(p);
    } catch (err) { fail(err); }
  };
  $("#demo").onclick = startDemo;
  const rs = $("#rec-settings"); if (rs) rs.onclick = () => go("settings");
  const rh = $("#rec-hide"); if (rh) rh.onclick = async () => { await saveSettings({ recommend_notice: false }); renderNew(); };
  bindHistory();
}
/* Start-screen card: strongly recommends Serper search and an AI model while either is missing. */
function recommendCard() {
  const k = S.settings.keys || {};
  const noSearch = !k.serper, noLlm = !k.anthropic && !k.openai;
  if (!S.settings.recommend_notice || (!noSearch && !noLlm)) return "";
  const link = (name) => `<a href="${KEY_URLS[name]}">${esc(t("app.recommend.get_" + name))}</a>`;
  const item = (title, text, links) => `<div class="warn"><span class="w-ico">!</span><div class="grow"><b>${esc(title)}</b>
    <div class="small" style="margin-top:2px">${esc(text)}</div><div class="small" style="margin-top:6px">${links}</div></div></div>`;
  return `<div class="card" id="recommend" style="margin-bottom:14px"><h2 style="margin:0 0 4px">${esc(t("app.recommend.title"))}</h2><p class="muted" style="margin:0">${esc(t("app.recommend.lead"))}</p>
    ${noSearch ? item(t("app.recommend.serper_title"), t("app.recommend.serper_text"), link("serper")) : ""}
    ${noLlm ? item(t("app.recommend.llm_title"), t("app.recommend.llm_text"), link("anthropic") + " · " + link("openai")) : ""}
    <p class="hint">${esc(t("app.recommend.footer"))}</p>
    <div class="row"><span class="grow"></span><button class="btn" id="rec-hide">${esc(t("app.recommend.hide"))}</button>
      <button class="btn primary" id="rec-settings">${esc(t("app.recommend.add_keys"))}</button></div></div>`;
}
async function withBusy(el, fn) {
  const old = el.innerHTML;
  el.innerHTML = `<div class="big-ico">${IC.spinner}</div><h2>${esc(t("app.new.opening"))}</h2>`;
  try { return await fn(); } finally { el.innerHTML = old; }
}
async function created(p) {
  S.project = p; S.estimate = null;
  S.projects = await call("list_projects");
  go("setup", p.id);
}
async function startDemo() {
  try { const p = await call("create_demo"); await created(p); } catch (e) { fail(e); }
}

/* ------------------------------------------------------------------ setup + estimate */
async function renderSetup() {
  if (!S.project || S.project.id !== S.pid) {
    try { S.project = await call("get_project", S.pid); } catch (e) { return fail(e); }
    S.estimate = S.project.estimate || null;
  }
  const p = S.project, m = p.meta || {}, o = p.options || {};
  const llm = S.settings.effective_llm, chain = S.settings.search_chain;
  const langs = S.boot.languages.map(l => `<option value="${l.code}" ${l.code === (o.report_lang || S.settings.effective_report_lang) ? "selected" : ""}>${esc(l.name)}</option>`).join("");
  $("#main").innerHTML = `<div class="row"><div class="grow"><h1>${esc(t("app.setup.title"))}</h1>
      <p class="lead" style="margin-bottom:14px">${esc(p.pdf_name)} · ${esc(t("app.setup.pages", { n: m.pages || "?" }))}${p.demo ? " · " + esc(t("app.setup.demo_note")) : ""}</p></div></div>
    <div class="grid2"><div>
      <div class="card"><h2>${esc(t("app.setup.document"))}</h2>
        <label class="f">${esc(t("app.setup.f_title"))}</label><input type="text" data-m="title" value="${esc(m.title || "")}">
        <div class="grid3" style="grid-template-columns:2fr 1fr">
          <div><label class="f">${esc(t("app.setup.f_author"))}</label><input type="text" data-m="author" value="${esc(m.author || "")}"></div>
          <div><label class="f">${esc(t("app.setup.f_date"))}</label><input type="text" data-m="date" placeholder="2015-06-01" value="${esc(m.date || "")}"></div></div>
        <div class="hint">${esc(t("app.setup.date_hint"))}</div>
        <div class="grid3" style="grid-template-columns:1fr 1fr">
          <div><label class="f">${esc(t("app.setup.f_body"))}</label><input type="text" data-m="body_pages" placeholder="12-150" value="${esc(m.body_pages || "")}"></div>
          <div><label class="f">${esc(t("app.setup.f_refs"))}</label><input type="text" data-m="ref_pages" placeholder="151-170" value="${esc(m.ref_pages || "")}"></div></div>
        <div class="hint">${esc(t("app.setup.pages_hint"))}</div>
        <details><summary>${esc(t("app.common.advanced"))}</summary>
          <label class="f">${esc(t("app.setup.f_markers"))}</label><input type="text" data-m="author_markers" value="${esc(m.author_markers || "")}">
          <div class="hint">${esc(t("app.setup.markers_hint"))}</div></details>
      </div>
      <div class="card"><h2>${esc(t("app.setup.options"))}</h2>
        <label class="f">${esc(t("app.setup.report_lang"))}</label><select class="f" data-o="report_lang">${langs}</select>
        <div class="hint">${esc(t("app.setup.report_lang_hint"))}</div>
        <label class="check"><input type="checkbox" data-o="include_refs" ${o.include_refs ? "checked" : ""} ${p.demo ? "disabled" : ""}><span>${esc(t("app.setup.include_refs"))}<div class="hint">${esc(t("app.setup.include_refs_hint"))}</div></span></label>
        ${llm.provider !== "none" && !p.demo ? `<label class="check"><input type="checkbox" data-o="llm_off" ${o.llm_off ? "checked" : ""}><span>${esc(t("app.setup.llm_off"))}<div class="hint">${esc(t("app.setup.llm_off_hint", { model: llm.model }))}</div></span></label>` : ""}
        <label class="f">${esc(t("app.setup.budget"))}</label><input type="number" min="0" step="100" data-o="search_budget" value="${esc(o.search_budget ?? S.settings.search_budget)}" style="max-width:160px">
        <div class="hint">${esc(t("app.setup.budget_hint"))}</div>
        <h3 style="margin-top:16px">${esc(t("app.setup.local_title"))}</h3><div class="hint">${esc(t("app.setup.local_hint"))}</div>
        <ul class="sources-list">${(p.local_sources || []).map(n => `<li><span>${esc(n)}</span><button class="btn link danger" data-rm="${esc(n)}">${IC.close}</button></li>`).join("") || `<li class="muted">${esc(t("app.setup.local_none"))}</li>`}</ul>
        <button class="btn small" id="add-local">${esc(t("app.setup.local_add"))}</button>
      </div>
    </div><div>
      <div class="card" id="est-card">${estimateHtml()}</div>
      <div class="card"><h3>${esc(t("app.setup.services"))}</h3>${summaryOfSetup(p.demo)}
        ${!chain.length && !p.demo ? `<div class="warn err"><span class="w-ico">!</span><div class="grow">${esc(t("error.search.no_key"))}</div><button class="btn small" data-go="settings">${esc(t("app.nav.settings"))}</button></div>` : ""}
        ${llm.provider === "none" && !p.demo ? `<div class="warn"><span class="w-ico">i</span><div class="grow">${esc(t("app.setup.no_llm_warn"))}</div><button class="btn small" data-go="settings">${esc(t("app.nav.settings"))}</button></div>` : ""}
        <div class="row" style="margin-top:12px"><button class="btn link" data-go="settings">${esc(t("app.setup.change_services"))}</button></div>
      </div>
      <div class="row" style="margin-top:16px"><button class="btn danger" id="del">${esc(t("app.common.delete"))}</button><span class="grow"></span>
        <button class="btn primary big" id="start" ${(!chain.length && !p.demo) || S.estimating ? "disabled" : ""}>${esc(t("app.setup.start"))}</button></div>
    </div></div>`;
  let timer;
  const save = (fields, reestimate) => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try { S.project = await call("update_project", p.id, fields); if (reestimate) runEstimate(); } catch (e) { fail(e); }
    }, 300);
  };
  $$("[data-m]").forEach(el => el.addEventListener("change", () => save({ meta: { [el.dataset.m]: el.value.trim() } }, ["body_pages", "ref_pages"].includes(el.dataset.m))));
  $$("[data-o]").forEach(el => el.addEventListener("change", () => {
    const v = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value || 0) : el.value;
    save({ options: { [el.dataset.o]: v } }, ["include_refs", "llm_off", "search_budget"].includes(el.dataset.o));
  }));
  $$("[data-go]").forEach(b => b.onclick = () => go(b.dataset.go));
  $$("[data-rm]").forEach(b => b.onclick = async () => { try { S.project = await call("remove_local_source", p.id, b.dataset.rm); renderSetup(); } catch (e) { fail(e); } });
  $("#add-local").onclick = async () => { try { S.project = await call("add_local_sources", p.id); renderSetup(); } catch (e) { fail(e); } };
  $("#del").onclick = async () => {
    if (!await confirmBox(t("app.history.delete_title"), t("app.history.delete_text"), t("app.common.delete"), "danger")) return;
    try { await call("delete_project", p.id); S.projects = await call("list_projects"); go("new"); } catch (e) { fail(e); }
  };
  $("#start").onclick = startRun;
  bindEstimate();
  if (!S.estimate && !S.estimating) runEstimate();
}
function estimateHtml() {
  const e = S.estimate;
  if (S.estimating) return `<h2>${esc(t("app.est.title"))}</h2><div class="muted">${IC.spinner} ${esc(t("app.est.calculating"))}</div>`;
  if (!e) return `<h2>${esc(t("app.est.title"))}</h2><button class="btn" id="est-run">${esc(t("app.est.calculate"))}</button>`;
  if (e.error) return `<h2>${esc(t("app.est.title"))}</h2><div class="warn err"><span class="w-ico">!</span><div class="grow">${esc(errText(e.error))}</div></div><button class="btn" id="est-run" style="margin-top:10px">${esc(t("app.est.recalculate"))}</button>`;
  const engine = t("app.engine." + e.engine);
  const free = e.free_credits ? t("app.est.free_credits", { n: fmt(e.free_credits), engine }) : "";
  const searchCost = e.engine === "ddg" || e.engine === "none" ? t("app.est.free") : (e.search_usd ? usd(e.search_usd) : t("app.est.within_free"));
  const llm = e.llm;
  const items = [
    [t("app.est.pages"), `${e.body_pages ? e.body_pages.join("–") : "?"}`, t("app.est.of_pages", { n: e.pages })],
    [t("app.est.sentences"), fmt(e.sentences), t("app.est.words", { n: fmt(e.words) })],
    [t("app.est.queries"), fmt(e.queries), e.cached ? t("app.est.cached", { n: fmt(e.cached) }) : ""],
    [t("app.est.engine"), engine, free],
    [t("app.est.search_cost"), searchCost, e.engine !== "ddg" && e.engine !== "none" ? t("app.est.per1000", { usd: usd(e.search_per1000) }) : ""],
    [t("app.est.llm_cost"), llm ? `${usd(llm.usd_low)} – ${usd(llm.usd_high)}` : t("app.est.no_llm"), llm ? `${llm.model} · ~${fmt(llm.sources)} ${t("app.est.sources")}` : ""],
  ];
  const budgetNote = e.budget && e.engine !== "none" && e.run_now < e.new ? `<div class="warn"><span class="w-ico">i</span><div>${esc(t("app.est.budget_note", { run: fmt(e.run_now), total: fmt(e.new) }))}</div></div>` : "";
  return `<div class="row"><h2 class="grow">${esc(t("app.est.title"))}</h2><button class="btn link" id="est-run">${IC.refresh} ${esc(t("app.est.recalculate"))}</button></div>
    <div class="est-grid">${items.map(([k, v, s]) => `<div><div class="muted small">${esc(k)}</div><div class="v">${esc(v)}</div><div class="hint">${esc(s || "")}</div></div>`).join("")}</div>
    ${budgetNote}
    <div class="est-total"><div><div class="muted small">${esc(t("app.est.total"))}</div><div class="v">${e.total_usd_high ? `${usd(e.total_usd_low)} – ${usd(e.total_usd_high)}` : esc(t("app.est.free"))}</div></div>
      <div style="text-align:right"><div class="muted small">${esc(t("app.est.time"))}</div><div class="v">≈ ${esc(durMin(e.minutes))}</div></div></div>
    <div class="hint" style="margin-top:8px">${esc(t("app.est.disclaimer"))}</div>`;
}
function bindEstimate() { const b = $("#est-run"); if (b) b.onclick = runEstimate; }
async function runEstimate() {
  if (!S.project) return;
  S.estimating = true; refreshEstimate();
  try { S.estimate = await call("estimate", S.project.id); } catch (e) { S.estimate = { error: e }; }
  S.estimating = false; refreshEstimate();
}
function refreshEstimate() {
  const c = $("#est-card"); if (!c || S.screen !== "setup") return;
  c.innerHTML = estimateHtml(); bindEstimate();
  const s = $("#start"); if (s) s.disabled = S.estimating || (!S.settings.search_chain.length && !S.project.demo);
}
async function startRun() {
  const pid = S.pid;
  if (S.run && ["running", "paused", "starting"].includes(S.run.status) && S.run.pid !== pid) return toast(t("error.busy"), { bad: true });
  try {
    S.log = []; S.logPid = pid; S.run = { pid, status: "starting", steps: {}, order: [], percent: 0, warnings: [] };
    await call("start", pid);
    go("progress", pid);
  } catch (e) { S.run = null; fail(e); }
}

/* ------------------------------------------------------------------ progress */
window.__plagcheckEvents = function (payload) {
  const { events, state } = payload;
  if (state) onRunState(state, true);
  let logs = [];
  for (const ev of events) {
    if (ev.type === "log" && ev.pid === S.logPid) logs.push(ev);
    if (["run_done", "run_failed", "run_cancelled"].includes(ev.type)) onRunEnd(ev);
  }
  logs = logs.map(e => ({ ts: e.ts, level: e.level, msg: e.message, step: e.step }));
  if (logs.length) { S.log.push(...logs); if (S.log.length > 4000) S.log.splice(0, S.log.length - 4000); appendLog(logs); }
  if (S.screen === "progress" && S.pid === (state && state.pid)) scheduleProgress();
  updateNavActive();
};
function onRunState(st, fromEvent) {
  S.run = st;
  if (!fromEvent && st.log) { S.log = st.log; S.logPid = st.pid; }
}
async function onRunEnd(ev) {
  try { S.projects = await call("list_projects"); } catch (e) { /* ignore */ }
  const p = S.projects.find(x => x.id === ev.pid);
  const name = p ? p.name : "";
  if (ev.type === "run_done") toast(t("app.progress.toast_done", { name }), { action: { label: t("app.progress.open_report"), run: () => go("report", ev.pid) } });
  else if (ev.type === "run_failed") toast(t("app.progress.toast_failed", { name }), { bad: true });
  if (S.screen === "progress") renderProgress();
}
let progTimer = null;
function scheduleProgress() { if (progTimer) return; progTimer = requestAnimationFrame(() => { progTimer = null; updateProgress(); }); }

async function renderProgress() {
  const pid = S.pid;
  if (!pid) return go("history");
  let p;
  try { p = await call("get_project", pid); } catch (e) { return fail(e); }
  S.project = p;
  if (!S.run || S.run.pid !== pid || !p.active) {
    try { const st = await call("run_state", pid, true); onRunState(st); } catch (e) { return fail(e); }
  } else if (S.logPid !== pid) {
    const st = await call("run_state", pid, true); S.log = st.log || []; S.logPid = pid;
  }
  if (S.screen !== "progress" || S.pid !== pid) return;
  $("#main").innerHTML = `<div id="prog-banner"></div>
    <div class="card"><div class="prog-head"><div style="min-width:0"><h1 style="overflow:hidden;text-overflow:ellipsis">${esc(p.name)}</h1>
      <div class="muted small">${esc(p.pdf_name)} · ${esc(when(p.created))}</div></div><div class="row" id="prog-ctl"></div></div>
      <div class="overall"><div class="bar big" id="ov-bar"><i style="width:0"></i></div></div>
      <div class="overall-meta" id="ov-meta"></div>
      <div class="now" id="now"></div></div>
    <div class="spacer"></div>
    <div class="grid2"><div class="card"><h2>${esc(t("app.progress.steps"))}</h2><div class="steplist" id="steps"></div></div>
      <div><div class="card"><h2>${esc(t("app.progress.cost"))}</h2><div id="cost"></div></div>
        <div class="card" id="warn-card"><h2>${esc(t("app.progress.warnings"))}</h2><div id="warns"></div></div></div></div>
    <div class="spacer"></div>
    <div class="card"><div class="row" style="margin-bottom:8px"><h2 class="grow" style="margin:0">${esc(t("app.progress.log"))}</h2></div>
      <div class="log-tools"><div class="seg" id="log-level">${["all", "warning", "error"].map(l => `<button data-l="${l}" class="${S.logLevel === l ? "on" : ""}">${esc(t("app.progress.log_" + l))}</button>`).join("")}</div>
        <input type="text" id="log-q" placeholder="${esc(t("app.progress.log_search"))}" value="${esc(S.logQuery)}">
        <label class="check" style="margin:0"><input type="checkbox" id="log-auto" ${S.autoscroll ? "checked" : ""}><span class="small">${esc(t("app.progress.autoscroll"))}</span></label>
        <button class="btn small" id="log-copy">${esc(t("app.progress.copy"))}</button><button class="btn small" id="log-save">${esc(t("app.progress.save"))}</button></div>
      <div id="log"></div></div>`;
  $$("#log-level button").forEach(b => b.onclick = () => { S.logLevel = b.dataset.l; $$("#log-level button").forEach(x => x.classList.toggle("on", x === b)); renderLog(); });
  $("#log-q").oninput = e => { S.logQuery = e.target.value; renderLog(); };
  $("#log-auto").onchange = e => { S.autoscroll = e.target.checked; };
  $("#log-copy").onclick = () => { navigator.clipboard.writeText(filteredLog().map(logLine).join("\n")).then(() => toast(t("app.progress.copied"))); };
  $("#log-save").onclick = () => call("save_log", pid).then(r => r.path && toast(t("app.common.saved_to", { path: r.path }))).catch(fail);
  renderLog();
  updateProgress();
}
const ACTIVE = s => ["running", "paused", "starting"].includes(s);
function updateProgress() {
  const st = S.run, p = S.project;
  if (!st || !p || S.screen !== "progress" || st.pid !== S.pid || !$("#prog-banner")) return;
  const status = st.status || p.status;
  const active = ACTIVE(status) && p.active !== false;
  // banner
  let banner = "";
  if (!active) {
    if (status === "done") banner = bannerHtml("done", t("app.progress.done_title"), t("app.progress.done_text", { time: dur(st.elapsed_s) }), [["report", t("app.progress.open_report"), "primary"]]);
    else if (status === "failed") {
      const e = st.error || p.error || {};
      const settingsErr = /^(llm\.|search\.no_key|search\.unknown)/.test(e.code || "");
      const acts = settingsErr ? [["settings", t("app.nav.settings"), ""], ["resume", t("app.progress.retry"), "primary"]]
        : /^pdf\./.test(e.code || "") ? [["new", t("app.nav.new"), "primary"]] : [["resume", t("app.progress.retry"), "primary"]];
      banner = bannerHtml("failed", t("app.progress.failed_title"), errText(e), acts, e.traceback || (e.params && e.params.detail) || "");
    } else if (["cancelled", "interrupted"].includes(status)) {
      banner = bannerHtml(status, t(`app.progress.${status}_title`), t("app.progress.resume_text"), [["resume", t("app.progress.resume"), "primary"]]);
    }
  }
  $("#prog-banner").innerHTML = banner;
  $$("#prog-banner [data-act]").forEach(b => b.onclick = () => progressAction(b.dataset.act));
  const det = $("#prog-banner [data-copy]"); if (det) det.onclick = () => navigator.clipboard.writeText(det.dataset.copy).then(() => toast(t("app.progress.copied")));
  // controls
  $("#prog-ctl").innerHTML = active ? `<span class="pill ${status === "paused" ? "amber" : "blue"}">${esc(t("app.status." + (status === "starting" ? "running" : status)))}</span>
      ${status === "paused" ? `<button class="btn" data-act="resume_run">▶ ${esc(t("app.progress.continue"))}</button>` : `<button class="btn" data-act="pause">❚❚ ${esc(t("app.progress.pause"))}</button>`}
      <button class="btn danger" data-act="cancel">■ ${esc(t("app.progress.cancel"))}</button>`
    : `<span class="pill ${status === "done" ? "green" : status === "failed" ? "red" : "amber"}">${esc(t("app.status." + status))}</span>`;
  $$("#prog-ctl [data-act]").forEach(b => b.onclick = () => progressAction(b.dataset.act));
  // overall
  const bar = $("#ov-bar");
  bar.className = "bar big" + (status === "done" ? " done" : status === "failed" ? " failed" : status === "paused" ? " paused" : "");
  $("i", bar).style.width = (status === "done" ? 100 : st.percent || 0) + "%";
  const order = (st.order || []).filter(s => st.steps[s] && st.steps[s].status !== "skipped");
  const idx = order.indexOf(st.step) + 1;
  $("#ov-meta").innerHTML = `<span><b>${fmt(status === "done" ? 100 : st.percent || 0, 0)} %</b></span>
    ${idx > 0 && active ? `<span>${esc(t("app.progress.step_of", { n: idx, total: order.length }))}</span>` : ""}
    <span>${esc(t("app.progress.elapsed"))}: <b>${esc(dur(st.elapsed_s))}</b></span>
    ${active && st.eta_s != null ? `<span>${esc(t("app.progress.remaining"))}: <b>≈ ${esc(dur(st.eta_s))}</b></span>` : ""}`;
  // now
  const now = $("#now");
  now.hidden = !active;
  now.className = "now" + (status === "paused" ? " paused" : "");
  if (active) {
    const cur = st.current ? t("cur." + st.current.key, st.current.params || {}) : (st.step ? t("step." + st.step) + "…" : t("app.progress.starting"));
    now.innerHTML = `<span class="dot"></span><span class="txt">${esc(status === "paused" ? t("app.progress.paused_text") : cur)}</span>`;
  }
  // steps
  $("#steps").innerHTML = (st.order || []).map((s, i) => stepHtml(s, st.steps[s] || {}, i + 1)).join("") || `<div class="muted">${esc(t("app.progress.starting"))}</div>`;
  // cost
  $("#cost").innerHTML = costHtml(st);
  // warnings
  const ws = st.warnings || [];
  $("#warn-card").hidden = !ws.length;
  $("#warns").innerHTML = ws.slice().reverse().map(w => {
    const act = /^(search\.(engine_switched|engines_exhausted|too_many_errors)|llm\.)/.test(w.key) ? `<button class="btn small" data-go="settings">${esc(t("app.nav.settings"))}</button>` : "";
    return `<div class="warn"><span class="w-ico">!</span><div class="grow"><div>${esc(t("warning." + w.key, w.params || {}))}</div>
      <div class="hint">${esc(t("step." + (w.step || "")))} · ${esc(new Date(w.ts * 1000).toLocaleTimeString(locale()))}</div></div>${act}</div>`;
  }).join("");
  $$("#warns [data-go]").forEach(b => b.onclick = () => go(b.dataset.go));
}
function bannerHtml(kind, title, text, actions, details) {
  return `<div class="banner ${kind}"><div class="grow" style="min-width:0"><h2 style="margin:0 0 2px">${esc(title)}</h2><div>${esc(text)}</div>
    ${details ? `<details><summary>${esc(t("app.progress.details"))}</summary><pre class="mono" style="white-space:pre-wrap;max-height:220px;overflow:auto">${esc(details)}</pre>
      <button class="btn small" data-copy="${esc(details)}">${esc(t("app.progress.copy"))}</button></details>` : ""}</div>
    <div class="row">${actions.map(([a, l, c]) => `<button class="btn ${c}" data-act="${a}">${esc(l)}</button>`).join("")}</div></div>`;
}
const BAD = new Set(["errors", "error", "blocked", "llm_errors", "problem_pairs", "problem_sources", "not_found"]);
const GOOD = new Set(["ok", "found", "relevant", "results"]);
const SUMMARY_KEYS = {
  extract: ["pages", "sentences", "words", "references"], refs: ["found"], search: ["queries", "ran", "candidates", "selected", "errors"],
  fetch: ["sources", "ok", "blocked", "error"], align: ["relevant", "matched_pct"], verify: ["risk", "problem_pct", "usd"], report: ["kb"],
};
function stepHtml(name, s, n) {
  const status = s.status || "pending";
  const ico = { done: IC.check, failed: IC.close, cancelled: "■", skipped: "–", running: `${IC.spinner}` }[status] || n;
  let meta = "", body = "";
  if (status === "running") {
    meta = s.total ? `${fmt(s.done)} / ${fmt(s.total)}${s.rate ? ` · ${fmt(s.rate, s.rate < 10 ? 1 : 0)}/${t("app.unit.s")}` : ""}${s.eta_s != null ? ` · ≈ ${dur(s.eta_s)}` : ""}` : "";
    body = `<div class="muted small">${esc(s.phase ? t("phase." + s.phase) : "")}</div>
      <div class="bar ${s.total ? "" : "indet"}"><i style="width:${Math.round((s.frac || 0) * 100)}%"></i></div>${chips(s.counters)}`;
  } else if (status === "done") {
    meta = s.duration_s != null ? dur(s.duration_s) : "";
    body = summaryChips(name, s.summary || {});
  } else if (status === "failed" || status === "cancelled") {
    meta = s.duration_s != null ? dur(s.duration_s) : "";
    body = `<div class="small" style="color:var(--red)">${esc(status === "failed" ? errText(s.error) : t("app.status.cancelled"))}</div>`;
  } else if (status === "skipped") {
    body = `<div class="muted small">${esc(t("app.progress.skipped"))}</div>`;
  }
  return `<div class="step ${status}"><div class="st-ico">${ico}</div><div style="min-width:0">
    <div class="st-top"><span class="st-name">${esc(t("step." + name))}</span><span class="st-meta">${esc(meta)}</span></div>${body}</div></div>`;
}
function chips(c) {
  const e = Object.entries(c || {}).filter(([, v]) => v);
  if (!e.length) return "";
  return `<div class="chips">${e.map(([k, v]) => `<span class="chip ${BAD.has(k) ? "bad" : GOOD.has(k) ? "good" : ""}">${esc(t("counter." + k))}: <b>${fmt(v)}</b></span>`).join("")}</div>`;
}
function summaryChips(step, sum) {
  const keys = SUMMARY_KEYS[step] || [];
  const items = keys.filter(k => sum[k] != null && sum[k] !== "" && !(k === "usd" && !sum[k])).map(k => {
    let v = sum[k];
    if (k === "risk") v = t("risk." + ({ "žema": "low", "vidutinė": "medium", "aukšta": "high" }[v] || v));
    else if (k === "usd") v = usd(v);
    else if (/_pct$/.test(k)) v = fmt(v, 1) + " %";
    else if (typeof v === "number") v = fmt(v);
    else if (Array.isArray(v)) v = v.join("–");
    return `<span class="chip ${BAD.has(k) && sum[k] ? "bad" : ""}">${esc(t("app.sum." + k))}: <b>${esc(v)}</b></span>`;
  });
  return items.length ? `<div class="chips" style="margin-top:4px">${items.join("")}</div>` : "";
}
function costHtml(st) {
  const rows = [];
  const sc = (st.cost || {}).search || (st.steps.search || {}).cost;
  if (sc && sc.search) {
    for (const [eng, n] of Object.entries(sc.search)) rows.push([t("app.progress.cost_search", { engine: t("app.engine." + eng) }), t("app.progress.queries_n", { n: fmt(n) })]);
  }
  const vc = (st.cost || {}).verify;
  if (vc && (vc.input || vc.output)) rows.push([t("app.progress.cost_llm", { model: vc.model || "" }),
    `${usd(vc.usd)} <span class="muted small">(${fmt((vc.input || 0) / 1e3)}k / ${fmt((vc.output || 0) / 1e3)}k ${esc(t("app.progress.tokens"))})</span>`]);
  const vs = (st.steps.verify || {}).summary;
  if (!vc && vs && vs.usd) rows.push([t("app.progress.cost_llm", { model: vs.model || "" }), usd(vs.usd)]);
  const est = S.project && S.project.estimate;
  const estRow = est && est.total_usd_high ? `<div class="hint" style="margin-top:8px">${esc(t("app.progress.estimated", { cost: `${usd(est.total_usd_low)} – ${usd(est.total_usd_high)}` }))}</div>` : "";
  if (!rows.length) return `<div class="muted small">${esc(t("app.progress.no_cost"))}</div>${estRow}`;
  return rows.map(([k, v]) => `<div class="cost-row"><span>${esc(k)}</span><b>${v.includes("<span") ? v : esc(v)}</b></div>`).join("") + estRow;
}
async function progressAction(a) {
  try {
    if (a === "pause") await call("pause");
    else if (a === "resume_run") await call("resume");
    else if (a === "cancel") { if (await confirmBox(t("app.progress.cancel_title"), t("app.progress.cancel_text"), t("app.progress.cancel"), "danger")) await call("cancel"); }
    else if (a === "resume") { S.pid = S.project.id; await startRun(); }
    else if (a === "report") go("report", S.project.id);
    else go(a);
  } catch (e) { fail(e); }
}
const logLine = e => `${new Date(e.ts * 1000).toLocaleTimeString(locale())}  ${e.msg}`;
function filteredLog() {
  const q = S.logQuery.trim().toLowerCase();
  return S.log.filter(e => (S.logLevel === "all" || e.level === S.logLevel || (S.logLevel === "warning" && e.level === "error"))
    && (!q || (e.msg || "").toLowerCase().includes(q)));
}
function logNode(e) {
  const d = document.createElement("div");
  d.className = "l " + (e.level || "info");
  d.innerHTML = `<span class="ts">${esc(new Date(e.ts * 1000).toLocaleTimeString(locale()))}</span>${esc(e.msg)}`;
  return d;
}
function renderLog() {
  const box = $("#log"); if (!box) return;
  box.innerHTML = "";
  const list = filteredLog().slice(-1500);
  const frag = document.createDocumentFragment();
  list.forEach(e => frag.appendChild(logNode(e)));
  if (!list.length) frag.appendChild(Object.assign(document.createElement("div"), { className: "l log-empty", textContent: t("app.progress.log_empty") }));
  box.appendChild(frag);
  box.scrollTop = box.scrollHeight;
}
function appendLog(items) {
  const box = $("#log"); if (!box || S.screen !== "progress") return;
  const q = S.logQuery.trim().toLowerCase();
  const empty = $(".log-empty", box); if (empty) empty.remove();
  for (const e of items) {
    if (S.logLevel !== "all" && !(e.level === S.logLevel || (S.logLevel === "warning" && e.level === "error"))) continue;
    if (q && !(e.msg || "").toLowerCase().includes(q)) continue;
    box.appendChild(logNode(e));
  }
  while (box.childElementCount > 2000) box.removeChild(box.firstChild);
  if (S.autoscroll) box.scrollTop = box.scrollHeight;
}

/* ------------------------------------------------------------------ report */
async function renderReport() {
  const pid = S.pid;
  let p;
  try { p = await call("get_project", pid); } catch (e) { return fail(e); }
  S.project = p;
  const lang = S.reportLang || (p.options || {}).report_lang || S.settings.effective_report_lang;
  const fmts = ["html", "pdf", "csv", "json", "zip"];
  $("#main").innerHTML = `<div class="rep-bar"><button class="btn" id="rb-back">${IC.back} ${esc(t("app.nav.history"))}</button>
      <span class="title">${esc(p.name)}</span>${p.result && p.result.risk ? `<span class="pill ${p.result.risk}">${esc(t("risk." + p.result.risk))}</span>` : ""}
      <span class="grow"></span>
      <label class="muted small">${esc(t("app.report.language"))}</label>
      <select id="rb-lang" class="f" style="width:auto">${S.boot.languages.map(l => `<option value="${l.code}" ${l.code === lang ? "selected" : ""}>${esc(l.name)}</option>`).join("")}</select>
      <button class="btn" id="rb-progress">${esc(t("app.report.details"))}</button>
      <button class="btn" id="rb-browser">${esc(t("app.report.open_browser"))}</button>
      <div class="menu"><button class="btn primary" id="rb-export">${esc(t("app.report.export"))} ${IC.chev}</button>
        <div class="menu-list" id="rb-menu" hidden>${fmts.map(f => `<button data-f="${f}"><b>${f.toUpperCase()}</b><small>${esc(t("app.report.fmt_" + f))}</small></button>`).join("")}</div></div>
    </div><iframe id="report-frame" title="report" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-same-origin"></iframe>`;
  $("#rb-back").onclick = () => go("history");
  $("#rb-progress").onclick = () => go("progress", pid);
  $("#rb-lang").onchange = e => { S.reportLang = e.target.value; loadReport(pid, S.reportLang); };
  $("#rb-browser").onclick = () => call("open_report_in_browser", pid, $("#rb-lang").value).catch(fail);
  $("#rb-export").onclick = e => { e.stopPropagation(); $("#rb-menu").hidden = !$("#rb-menu").hidden; };
  $$("#rb-menu [data-f]").forEach(b => b.onclick = async () => {
    $("#rb-menu").hidden = true;
    try {
      toast(t("app.report.exporting"), { ms: 20000 });
      const r = await call("export", pid, b.dataset.f, $("#rb-lang").value);
      if (r.cancelled) { $("#toast").className = ""; return; }
      toast(t("app.common.saved_to", { path: r.path }), { action: { label: t("app.common.show_folder"), run: () => call("reveal", r.path) } });
    } catch (e) { fail(e); }
  });
  loadReport(pid, lang);
}
async function loadReport(pid, lang) {
  const fr = $("#report-frame"); if (!fr) return;
  fr.srcdoc = `<body style="font-family:system-ui;color:#888;padding:40px">${esc(t("app.report.loading"))}</body>`;
  try { const r = await call("report_html", pid, lang); if ($("#report-frame") === fr) fr.srcdoc = r.html; }
  catch (e) { fr.srcdoc = `<body style="font-family:system-ui;padding:40px;color:#c33">${esc(errText(e))}</body>`; }
}

/* ------------------------------------------------------------------ history */
function historyTable(list) {
  if (!list.length) return `<div class="empty">${esc(t("app.history.empty"))}</div>`;
  return `<table class="list"><thead><tr><th>${esc(t("app.history.name"))}</th><th>${esc(t("app.history.date"))}</th><th>${esc(t("app.history.status"))}</th><th>${esc(t("app.history.result"))}</th><th></th></tr></thead><tbody>
    ${list.map(p => {
      const active = S.run && S.run.pid === p.id && ACTIVE(S.run.status);
      const status = active ? S.run.status : p.status;
      const cls = { done: "green", failed: "red", running: "blue", paused: "amber", interrupted: "amber", cancelled: "amber" }[status] || "";
      const pct = active ? ` ${fmt(S.run.percent, 0)} %` : "";
      return `<tr class="click" data-pid="${p.id}"><td><b>${esc(p.name)}</b><div class="muted small">${esc(p.pdf_name || "")}</div></td>
        <td class="small">${esc(when(p.created))}</td><td><span class="pill ${cls}">${esc(t("app.status." + status))}${pct}</span></td>
        <td>${p.risk ? `<span class="pill ${p.risk}">${esc(t("risk." + p.risk))}</span> <span class="small muted">${p.problem_pct != null ? fmt(p.problem_pct, 1) + " %" : ""}</span>` : ""}</td>
        <td style="text-align:right"><button class="btn link danger" data-del="${p.id}" title="${esc(t("app.common.delete"))}">${IC.close}</button></td></tr>`;
    }).join("")}</tbody></table>`;
}
function bindHistory() {
  $$("tr[data-pid]").forEach(tr => tr.onclick = e => { if (!e.target.closest("[data-del]")) openProject(tr.dataset.pid); });
  $$("[data-del]").forEach(b => b.onclick = async () => {
    if (!await confirmBox(t("app.history.delete_title"), t("app.history.delete_text"), t("app.common.delete"), "danger")) return;
    try { await call("delete_project", b.dataset.del); S.projects = await call("list_projects"); render(); } catch (e) { fail(e); }
  });
}
async function renderHistory() {
  try { S.projects = await call("list_projects"); } catch (e) { fail(e); }
  $("#main").innerHTML = `<div class="row"><div class="grow"><h1>${esc(t("app.history.title"))}</h1><p class="lead">${esc(t("app.history.lead"))}</p></div>
    <button class="btn primary" id="h-new">＋ ${esc(t("app.nav.new"))}</button></div>
    <div class="card" style="padding:4px 10px">${historyTable(S.projects)}</div>
    <p class="hint">${esc(t("app.history.stored", { path: S.boot.data_dir }))} <button class="btn link" id="h-open">${esc(t("app.common.open_folder"))}</button></p>`;
  $("#h-new").onclick = () => go("new");
  $("#h-open").onclick = () => call("open_path", S.boot.data_dir).catch(fail);
  bindHistory();
}

/* ------------------------------------------------------------------ settings */
function renderSettings() {
  const s = S.settings;
  const langOpts = (sel, autoLabel) => (autoLabel ? `<option value="">${esc(autoLabel)}</option>` : "") +
    S.boot.languages.map(l => `<option value="${l.code}" ${l.code === sel ? "selected" : ""}>${esc(l.name)}</option>`).join("");
  // free-text model fields with suggestions (built-in list + models the key check returned)
  const modelList = (id, ...lists) => `<datalist id="${id}">${[...new Set(lists.flat().filter(Boolean))].map(m => `<option value="${esc(m)}"></option>`).join("")}</datalist>`;
  const modelInput = (key, list) => `<input type="text" class="f" data-s="${key}" list="${list}" spellcheck="false" autocomplete="off" value="${esc(s[key] || "")}">`;
  const anthropicModels = (S.models.anthropic || []).filter(m => /claude/.test(m));
  const openaiModels = (S.models.openai || []).filter(m => /^(gpt|o\d)/.test(m));
  $("#main").innerHTML = `<h1>${esc(t("app.settings.title"))}</h1><p class="lead">${esc(t("app.settings.lead"))}</p>
    <div class="grid2"><div>
      <div class="card"><h2>${esc(t("app.settings.search"))}</h2><p class="hint" style="margin-top:-4px">${esc(t("app.wizard.search_text"))}</p>
        ${keyRow("serper")}${keyRow("tavily")}
        <label class="check"><input type="checkbox" data-s="allow_free_search" ${s.allow_free_search ? "checked" : ""}><span>${esc(t("app.settings.allow_free"))}<div class="hint">${esc(t("app.settings.allow_free_hint"))}</div></span></label>
        <label class="f">${esc(t("app.setup.budget"))}</label><input type="number" min="0" step="100" data-s="search_budget" value="${esc(s.search_budget)}" style="max-width:160px">
        <div class="hint">${esc(t("app.setup.budget_hint"))}</div></div>
      <div class="card"><h2>${esc(t("app.settings.llm"))}</h2><p class="hint" style="margin-top:-4px">${esc(t("app.wizard.llm_text"))}</p>
        <label class="f">${esc(t("app.settings.provider"))}</label><select class="f" data-s="llm_provider">
          ${["auto", "anthropic", "openai", "none"].map(v => `<option value="${v}" ${s.llm_provider === v ? "selected" : ""}>${esc(t("app.settings.provider_" + v))}</option>`).join("")}</select>
        <div class="hint">${esc(t("app.settings.effective", { value: s.effective_llm.provider === "none" ? t("app.setup.no_llm") : t("app.engine." + s.effective_llm.provider) + " · " + s.effective_llm.model }))}</div>
        ${keyRow("anthropic")}
        <label class="f">${esc(t("app.settings.anthropic_model"))}</label>${modelInput("anthropic_model", "dl-anthropic")}${modelList("dl-anthropic", s.anthropic_models, anthropicModels)}
        ${keyRow("openai")}
        <label class="f">${esc(t("app.settings.openai_model"))}</label>${modelInput("openai_model", "dl-openai")}${modelList("dl-openai", s.openai_models, openaiModels)}
        <details><summary>${esc(t("app.common.advanced"))}</summary>
          <label class="f">${esc(t("app.settings.openai_base_url"))}</label><input type="text" data-s="openai_base_url" placeholder="https://api.openai.com/v1" value="${esc(s.openai_base_url || "")}">
          <div class="hint">${esc(t("app.settings.openai_base_url_hint"))}</div>
          <label class="f">${esc(t("app.settings.final_model"))}</label>${modelInput("final_model", "dl-all")}${modelList("dl-all", s.anthropic_models, anthropicModels, s.openai_models, openaiModels)}
          <div class="hint">${esc(t("app.settings.final_model_hint"))}</div></details></div>
    </div><div>
      <div class="card"><h2>${esc(t("app.settings.interface"))}</h2>
        <label class="f">${esc(t("app.language"))}</label><select class="f" data-s="language">${langOpts(s.effective_language)}</select>
        <label class="f">${esc(t("app.settings.report_lang"))}</label><select class="f" data-s="report_lang">${langOpts(s.report_lang || "", t("app.settings.same_as_ui"))}</select>
        <div class="hint">${esc(t("app.setup.report_lang_hint"))}</div></div>
      <div class="card"><h2>${esc(t("app.settings.sources"))}</h2>
        <label class="f">${esc(t("app.settings.unpaywall"))}</label><input type="email" data-s="unpaywall_email" placeholder="name@example.com" value="${esc(s.unpaywall_email || "")}">
        <div class="hint">${esc(t("app.settings.unpaywall_hint"))}</div>
        <label class="check"><input type="checkbox" data-s="use_wayback" ${s.use_wayback ? "checked" : ""}><span>${esc(t("app.settings.wayback"))}<div class="hint">${esc(t("app.settings.wayback_hint"))}</div></span></label>
        <label class="check"><input type="checkbox" data-s="include_refs" ${s.include_refs ? "checked" : ""}><span>${esc(t("app.setup.include_refs"))}<div class="hint">${esc(t("app.settings.include_refs_hint"))}</div></span></label></div>
      <div class="card"><h2>${esc(t("app.settings.behaviour"))}</h2>
        <label class="check"><input type="checkbox" data-s="notify" ${s.notify ? "checked" : ""}><span>${esc(t("app.settings.notify"))}</span></label>
        <label class="check"><input type="checkbox" data-s="keep_awake" ${s.keep_awake ? "checked" : ""}><span>${esc(t("app.settings.keep_awake"))}</span></label></div>
      <div class="card"><h2>${esc(t("app.settings.storage"))}</h2>
        <p class="small">${esc(t(s.secret_store === "keyring" ? "app.settings.store_keyring" : "app.settings.store_file", { path: s.folder }))}</p>
        <div class="row"><button class="btn small" id="open-data">${esc(t("app.common.open_folder"))}</button>
          <button class="btn small" id="rerun-wizard">${esc(t("app.settings.wizard"))}</button></div></div>
    </div></div>`;
  bindKeyRows($("#main"), renderSettings);
  // A datalist only suggests entries matching the current text, so clear the field on focus to show the
  // whole list; if nothing is typed or picked, the old value comes back on blur (no change event, no save).
  $$("input[list][data-s]").forEach(el => {
    el.addEventListener("focus", () => { el.dataset.prev = el.value; el.dataset.typed = ""; el.placeholder = el.value; el.value = ""; });
    el.addEventListener("input", () => { el.dataset.typed = "1"; });
    el.addEventListener("blur", () => { if (!el.dataset.typed && !el.value) el.value = el.dataset.prev || ""; el.placeholder = ""; });
  });
  $$("[data-s]").forEach(el => el.addEventListener("change", async () => {
    const k = el.dataset.s;
    const v = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value || 0) : el.value.trim() || (["language", "report_lang"].includes(k) ? null : "");
    await saveSettings({ [k]: v });
    if (["llm_provider", "anthropic_model", "openai_model"].includes(k) && S.screen === "settings") renderSettings();
    toast(t("app.common.saved"));
  }));
  $("#open-data").onclick = () => call("open_path", S.boot.data_dir).catch(fail);
  $("#rerun-wizard").onclick = () => { S.wizardStep = 0; go("wizard"); };
}

/* ------------------------------------------------------------------ about */
function renderAbout() {
  const repo = "https://github.com/" + S.boot.repo;
  $("#main").innerHTML = `<h1>${esc(t("app.name"))}</h1><p class="lead">${esc(t("app.about.lead"))}</p>
    <div class="grid2"><div class="card"><dl class="kv"><dt>${esc(t("app.about.version"))}</dt><dd>${esc(S.boot.version)}</dd>
      <dt>${esc(t("app.about.author"))}</dt><dd>${esc(S.boot.author)}</dd>
      <dt>${esc(t("app.about.system"))}</dt><dd>${esc(S.boot.platform)} · ${esc(S.boot.machine)}</dd>
      <dt>${esc(t("app.about.data"))}</dt><dd class="mono">${esc(S.boot.data_dir)}</dd></dl>
      <div class="row" style="margin-top:14px"><a class="btn" href="${SITE}">${esc(t("app.about.website"))}</a>
        <a class="btn" href="${repo}">GitHub</a><a class="btn" href="${repo}/releases/latest">${esc(t("app.about.updates"))}</a>
        <a class="btn" href="${repo}/issues">${esc(t("app.about.report_bug"))}</a></div></div>
    <div class="card"><h2>${esc(t("app.about.privacy_title"))}</h2><ul class="small" style="padding-left:18px;margin:0">
      <li>${esc(t("app.about.privacy1"))}</li><li>${esc(t("app.about.privacy2"))}</li><li>${esc(t("app.about.privacy3"))}</li><li>${esc(t("app.about.privacy4"))}</li></ul></div></div>
    <div class="card"><h2>${esc(t("app.about.limits_title"))}</h2><p class="small" style="margin:0">${esc(t("app.about.limits"))}</p></div>`;
}

/* ------------------------------------------------------------------ start */
if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot);
if (/[?&]mock\b/.test(location.search)) {
  const s = document.createElement("script"); s.src = "mock.js"; document.head.appendChild(s);
}
