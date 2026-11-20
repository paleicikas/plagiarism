/* Plagiarism website. No dependencies. English text is inline in index.html; other languages come from locales/*.json. */
(function () {
  "use strict";

  var LANGS = ["en", "lt"];                 // add a code here after adding site/locales/<code>.json
  var REPO = "paleicikas/plagiarism";
  var AUTHOR = "Artūras Paleičikas";
  var RELEASES = "https://github.com/" + REPO + "/releases";
  var $ = function (s, el) { return (el || document).querySelector(s); };
  var $$ = function (s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); };
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  };

  // ------------------------------------------------------------------ i18n
  var dict = {}, lang = "en";

  function lookup(key) {
    var cur = dict;
    var parts = key.split(".");
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== "object") return null;
      cur = cur[parts[i]];
    }
    return typeof cur === "string" ? cur : null;
  }
  function t(key, params) {
    var s = lookup(key);
    if (s == null) return key;
    return s.replace(/\{(\w+)\}/g, function (m, p) { return params && p in params ? params[p] : m; });
  }

  function pickLang() {
    var q = new URLSearchParams(location.search).get("lang");
    if (q && LANGS.indexOf(q) >= 0) return q;
    var saved = store.get("plagcheck-lang");
    if (saved && LANGS.indexOf(saved) >= 0) return saved;
    var prefs = navigator.languages || [navigator.language || "en"];
    for (var i = 0; i < prefs.length; i++) {
      var c = String(prefs[i]).toLowerCase().split("-")[0];
      if (LANGS.indexOf(c) >= 0) return c;
    }
    return "en";
  }

  function applyTexts() {
    $$("[data-t]").forEach(function (el) { var v = lookup(el.dataset.t); if (v != null) el.textContent = v; });
    $$("[data-th]").forEach(function (el) { var v = lookup(el.dataset.th); if (v != null) el.innerHTML = v; });
    $$("[data-ta]").forEach(function (el) {
      el.dataset.ta.split(",").forEach(function (pair) {
        var p = pair.split(":"), v = lookup(p[1]);
        if (v != null) el.setAttribute(p[0], v);
      });
    });
    document.documentElement.lang = lang;
    $$(".langsw button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.lang === lang)); });
    $$("img[data-shot]").forEach(function (img) {
      var en = "assets/screens/en/" + img.dataset.shot + ".png";
      img.onerror = function () { img.onerror = null; img.src = en; };
      img.src = "assets/screens/" + lang + "/" + img.dataset.shot + ".png";
    });
  }

  function setLang(code, remember) {
    return fetch("locales/" + code + ".json", { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        dict = d; lang = code;
        if (remember) {
          store.set("plagcheck-lang", code);
          var u = new URL(location.href);
          if (u.searchParams.has("lang")) { u.searchParams.set("lang", code); history.replaceState(null, "", u); }
        }
        applyTexts(); renderDownload(); calc(); showExample(currentEx);
      })
      .catch(function (e) {
        console.warn("Plagiarism site: could not load language " + code, e);
        if (code !== "en" && !lookup("_meta.name")) return setLang("en", false);
      });
  }

  function buildLangSwitchers() {
    $$(".langsw").forEach(function (box) {
      LANGS.forEach(function (c) {
        var b = document.createElement("button");
        b.type = "button"; b.dataset.lang = c; b.textContent = c.toUpperCase(); b.setAttribute("aria-pressed", "false");
        b.addEventListener("click", function () { setLang(c, true); });
        box.appendChild(b);
      });
    });
  }

  // ------------------------------------------------------------------ theme and menu
  function setupTheme() {
    $("#theme").addEventListener("click", function () {
      var root = document.documentElement;
      var dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      store.set("plagcheck-theme", root.dataset.theme);
    });
  }

  function setupMenu() {
    var nav = $("#nav"), btn = $("#menu");
    btn.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      btn.setAttribute("aria-expanded", String(open));
    });
    $$("a", nav).forEach(function (a) { a.addEventListener("click", function () { nav.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); }); });
    if (!("IntersectionObserver" in window)) return;
    var links = {};
    $$("a", nav).forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && links[e.target.id]) {
          $$("a.on", nav).forEach(function (a) { a.classList.remove("on"); });
          links[e.target.id].classList.add("on");
        }
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    Object.keys(links).forEach(function (id) { var s = document.getElementById(id); if (s) io.observe(s); });
  }

  // ------------------------------------------------------------------ downloads
  var os = { name: "windows", arch: null }, release = null, releaseFailed = false;

  function detectOS() {
    var ua = navigator.userAgent, p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    // Windows is the default; macOS and Linux are offered only when the visitor is clearly on them
    if (/iPhone|iPad|Android/.test(ua)) os.name = "windows";
    else if (/mac/i.test(p) || /Mac OS X/.test(ua)) os.name = "mac";
    else if (/linux/i.test(p) || /Linux|X11|CrOS/.test(ua)) os.name = "linux";
    else os.name = "windows";
    if (os.name === "mac" && navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
      navigator.userAgentData.getHighEntropyValues(["architecture"]).then(function (v) {
        os.arch = v.architecture === "x86" ? "x64" : "arm64";
        renderDownload();
      }).catch(function () {});
    }
    $$(".os-notes li").forEach(function (li) { li.classList.toggle("mine", li.dataset.os === os.name); });
  }

  function loadRelease() {
    var cached = null;
    try { cached = JSON.parse(sessionStorage.getItem("plagcheck-release") || "null"); } catch (e) {}
    if (cached && Date.now() - cached.at < 10 * 60 * 1000) { release = cached.data; renderDownload(); return; }
    fetch("https://api.github.com/repos/" + REPO + "/releases/latest", { headers: { Accept: "application/vnd.github+json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        release = { tag: d.tag_name, date: d.published_at, assets: (d.assets || []).map(function (a) { return { name: a.name, url: a.browser_download_url, size: a.size }; }) };
        try { sessionStorage.setItem("plagcheck-release", JSON.stringify({ at: Date.now(), data: release })); } catch (e) {}
        renderDownload();
      })
      .catch(function () { releaseFailed = true; renderDownload(); });
  }

  function asset(suffix) {
    if (!release) return null;
    for (var i = 0; i < release.assets.length; i++) if (release.assets[i].name.slice(-suffix.length) === suffix) return release.assets[i];
    return null;
  }

  function mb(bytes) { return Math.round(bytes / 1048576) + " MB"; }

  function renderDownload() {
    if (!dict._meta) return;                 // called again once the texts are loaded
    var main = $("#dl-main"), label = $("#dl-main-label");
    var pick = { windows: ["windows-x64-setup.exe", "windows"], linux: [".AppImage", "linux"],
                 mac: [os.arch === "x64" ? "macos-x64.dmg" : "macos-arm64.dmg", os.arch === "x64" ? "mac_intel" : os.arch === "arm64" ? "mac_arm" : "mac"] }[os.name];
    var a = pick && asset(pick[0]);
    if (pick) label.textContent = t("hero.download", { os: t("os." + pick[1]) });
    main.href = a ? a.url : RELEASES + "/latest";
    $$("[data-asset]").forEach(function (el) {
      var x = asset(el.dataset.asset);
      el.href = x ? x.url : RELEASES + "/latest";
      el.hidden = !!release && !x;           // e.g. no Intel macOS build in this release
      var old = $(".size", el); if (old) old.remove();
      if (x && x.size > 2e6) { var s = document.createElement("span"); s.className = "size"; s.textContent = " · " + mb(x.size); el.appendChild(s); }
    });
    var ver = $("#dl-version"), foot = $("#foot-version");
    if (release) {
      var v = release.tag.replace(/^v/, "");
      var date = new Date(release.date).toLocaleDateString((dict._meta && dict._meta.locale) || "en-GB", { year: "numeric", month: "long", day: "numeric" });
      ver.textContent = t("hero.version", { version: v, date: date });
      foot.textContent = "v" + v + ", " + AUTHOR;
    } else if (releaseFailed) {
      ver.textContent = t("hero.no_release");
    }
  }

  // ------------------------------------------------------------------ cost calculator (same formulas as plagcheck/estimate.py)
  var LLM_PRICES = [["opus", 5.0, 25.0], ["sonnet", 3.0, 15.0], ["haiku", 1.0, 5.0], ["gpt-4o-mini", 0.15, 0.6], ["gpt-4o", 2.5, 10.0],
                    ["gpt-4.1-mini", 0.4, 1.6], ["gpt-4.1", 2.0, 8.0], ["gpt-5-mini", 0.25, 2.0], ["gpt-5", 1.25, 10.0]];
  var TOKENS_PER_SOURCE = [4300, 1400], TOKENS_FINAL = [6000, 2500], SOURCES_PER_WORD = 1 / 1800, SELECTED_PER_QUERY = 0.4;
  // queries per word of main text, measured on a 165-page thesis (43,730 words -> 2,070 queries)
  var QUERIES_PER_WORD = 2070 / 43730;
  var ENGINES = { serper: { per1000: 1.0, free: 2500 }, tavily: { per1000: 8.0, free: 1000 }, ddg: { per1000: 0, free: null } };
  var SEARCH_WORKERS = 6, LLM_WORKERS = 4, MAX_SOURCES = 900;

  function price(model) {
    for (var i = 0; i < LLM_PRICES.length; i++) if (model.indexOf(LLM_PRICES[i][0]) >= 0) return LLM_PRICES[i];
    return [null, 3.0, 15.0];
  }
  function usd(v) {
    var loc = (dict._meta && dict._meta.locale) || "en-GB";
    return new Intl.NumberFormat(loc, { style: "currency", currency: "USD", currencyDisplay: "narrowSymbol", minimumFractionDigits: v < 10 ? 2 : 0, maximumFractionDigits: v < 10 ? 2 : 0 }).format(v);
  }
  function num(v) { return new Intl.NumberFormat((dict._meta && dict._meta.locale) || "en-GB").format(v); }

  function calc() {
    var pages = +$("#c-pages").value, wpp = Math.max(50, +$("#c-wpp").value || 265);
    var engine = $("#c-engine").value, model = $("#c-model").value, fresh = $("#c-fresh").checked;
    $("#c-pages-out").textContent = pages;
    $("#c-fresh").disabled = engine !== "serper";
    var words = pages * wpp;
    var queries = Math.round(words * QUERIES_PER_WORD);
    var e = ENGINES[engine];
    var free = engine === "serper" ? (fresh ? e.free : 0) : (e.free || 0);
    var searchUsd = Math.round(Math.max(queries - free, 0) / 1000 * e.per1000 * 100) / 100;

    var nSources = Math.max(5, Math.min(80, Math.round(words * SOURCES_PER_WORD)));
    var llm = null;
    if (model !== "none") {
      var p = price(model);
      var iTok = nSources * TOKENS_PER_SOURCE[0] + TOKENS_FINAL[0], oTok = nSources * TOKENS_PER_SOURCE[1] + TOKENS_FINAL[1];
      var v = iTok / 1e6 * p[1] + oTok / 1e6 * p[2];
      llm = { low: v * 0.5, high: v * 1.6 };
    }
    var qRate = engine === "ddg" ? 1.0 : Math.min(SEARCH_WORKERS, 6) * 1.3;
    var nFetch = Math.min(MAX_SOURCES, Math.round(queries * SELECTED_PER_QUERY));
    var secs = queries / qRate + nFetch / 0.8 + (llm ? nSources * 25 / LLM_WORKERS + 60 : 5) + 30;
    var minutes = Math.round(secs / 60);

    $("#o-queries").textContent = num(queries);
    $("#o-search").textContent = searchUsd > 0 ? usd(searchUsd) : t("cost.free");
    $("#o-ai").textContent = llm ? t("cost.range", { low: usd(llm.low), high: usd(llm.high) }) : "–";
    var lo = searchUsd + (llm ? llm.low : 0), hi = searchUsd + (llm ? llm.high : 0);
    $("#o-total").textContent = hi === 0 ? t("cost.free") : t("cost.range", { low: usd(lo), high: usd(hi) });
    $("#o-time").textContent = t("cost.about", { v: minutes < 60 ? t("cost.minutes", { n: minutes }) : t("cost.hours", { h: Math.floor(minutes / 60), m: minutes % 60 }) });
    $("#o-sources").textContent = llm ? t("cost.sources", { n: nSources }) : "";
  }

  function setupCalc() {
    ["#c-pages", "#c-wpp", "#c-engine", "#c-model", "#c-fresh"].forEach(function (s) {
      $(s).addEventListener("input", calc); $(s).addEventListener("change", calc);
    });
  }

  // ------------------------------------------------------------------ sentence-pair example (simplified plagcheck/align.py)
  var STOP = {};
  ("a about after also an and any are as at be because been but by can could did do does for from had has have he her his how " +
   "however i if in into is it its may more most no not of on or our she should so some such than that the their them then there " +
   "these they this those to was we were what when where which while who will with would you according").split(" ")
    .forEach(function (w) { STOP[w] = 1; });
  var LABEL = { e1: "pazodinis_be_nuorodos", e2: "pazodinis_be_nuorodos", e3: "perfrazuota_be_nuorodos", e4: "tinkamas_citavimas" };
  var currentEx = "e1";

  function tokenize(s) {
    var out = [], re = /[\p{L}\p{N}]+/gu, m;
    while ((m = re.exec(s))) out.push({ w: m[0].normalize("NFD").replace(/\p{M}/gu, "").toLowerCase(), start: m.index, end: m.index + m[0].length });
    return out;
  }

  // matching blocks like difflib.SequenceMatcher: longest common run, then recurse on both sides
  function blocks(a, b, alo, ahi, blo, bhi, out) {
    var best = 0, bi = 0, bj = 0;
    for (var i = alo; i < ahi; i++) for (var j = blo; j < bhi; j++) {
      var k = 0;
      while (i + k < ahi && j + k < bhi && a[i + k] === b[j + k]) k++;
      if (k > best) { best = k; bi = i; bj = j; }
    }
    if (!best) return out;
    blocks(a, b, alo, bi, blo, bj, out);
    out.push([bi, bj, best]);
    blocks(a, b, bi + best, ahi, bj + best, bhi, out);
    return out;
  }

  function highlight(text, toks, marked) {
    var html = "", pos = 0, i = 0;
    var esc = function (s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
    while (i < toks.length) {
      if (!marked[i]) { i++; continue; }
      var j = i;
      while (j + 1 < toks.length && marked[j + 1]) j++;
      html += esc(text.slice(pos, toks[i].start)) + "<mark>" + esc(text.slice(toks[i].start, toks[j].end)) + "</mark>";
      pos = toks[j].end; i = j + 1;
    }
    return html + esc(text.slice(pos));
  }

  function categorize(cov, lcs, cont, ncont) {
    if (cov >= 0.9) return "identical";
    if (cov >= 0.6) return "minor_edit";
    if (cov >= 0.35 && lcs >= 6) return "partial";
    if (cont >= 0.6 && ncont >= 6 && (cov >= 0.12 || lcs >= 3)) return "paraphrase";
    return "none";
  }

  function showExample(id) {
    currentEx = id;
    var doc = t("examples." + id + ".doc"), src = t("examples." + id + ".src");
    var dt = tokenize(doc), st = tokenize(src);
    var a = dt.map(function (x) { return x.w; }), b = st.map(function (x) { return x.w; });
    var bl = blocks(a, b, 0, a.length, 0, b.length, []).filter(function (x) { return x[2] >= 3; });
    var dm = [], sm = [], matched = 0, lcs = 0;
    bl.forEach(function (x) {
      for (var k = 0; k < x[2]; k++) { dm[x[0] + k] = 1; sm[x[1] + k] = 1; }
      matched += x[2]; lcs = Math.max(lcs, x[2]);
    });
    var content = function (ws) { var s = {}; ws.forEach(function (w) { if (w.length > 2 && !STOP[w] && !/^\d+$/.test(w)) s[w] = 1; }); return s; };
    var dc = content(a), sc = content(b), nd = Object.keys(dc).length;
    var shared = Object.keys(dc).filter(function (w) { return sc[w]; }).length;
    var cov = matched / Math.max(a.length, 1), cont = shared / Math.max(nd, 1);
    var cat = categorize(cov, lcs, cont, nd);

    $("#ex-doc").innerHTML = highlight(doc, dt, dm);
    $("#ex-src").innerHTML = highlight(src, st, sm);
    $("#ex-cov").textContent = Math.round(cov * 100) + " %";
    $("#ex-cont").textContent = Math.round(cont * 100) + " %";
    $("#ex-cat").textContent = t("cat." + cat);
    var lab = $("#ex-label");
    lab.textContent = t("label." + LABEL[id]);
    lab.className = LABEL[id] === "tinkamas_citavimas" ? "good" : "bad";
    $$("#ex-tabs button").forEach(function (btn) { btn.setAttribute("aria-selected", String(btn.dataset.ex === id)); });
  }

  function setupExamples() {
    $$("#ex-tabs button").forEach(function (btn) { btn.addEventListener("click", function () { showExample(btn.dataset.ex); }); });
  }

  // ------------------------------------------------------------------ screenshot lightbox
  function setupGallery() {
    var dlg = $("#lightbox"), shots = $$(".gallery .shot"), idx = 0;
    if (!dlg || !dlg.showModal) return;
    function show(i) {
      idx = (i + shots.length) % shots.length;
      var img = $("img", shots[idx]);
      $("img", dlg).src = img.currentSrc || img.src;
      $(".lb-cap", dlg).textContent = shots[idx].parentNode.querySelector("figcaption").textContent;
    }
    shots.forEach(function (b, i) { b.addEventListener("click", function () { show(i); dlg.showModal(); }); });
    $(".lb-close", dlg).addEventListener("click", function () { dlg.close(); });
    $(".lb-prev", dlg).addEventListener("click", function () { show(idx - 1); });
    $(".lb-next", dlg).addEventListener("click", function () { show(idx + 1); });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") show(idx - 1);
      if (e.key === "ArrowRight") show(idx + 1);
    });
  }

  // ------------------------------------------------------------------ start
  buildLangSwitchers();
  setupTheme();
  setupMenu();
  detectOS();
  setupCalc();
  setupExamples();
  setupGallery();
  loadRelease();
  setLang(pickLang(), false);

  // ------------------------------------------------------------------ sections fade in as they scroll into view
  (function () {
    if (!("IntersectionObserver" in window) || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var els = document.querySelectorAll(".sec .wrap > *");
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { rootMargin: "0px 0px -8% 0px" });
    els.forEach(function (el) { el.classList.add("reveal"); io.observe(el); });
  })();
})();
