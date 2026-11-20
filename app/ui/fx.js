/* Visual effects only (no app logic): Material ripples and the liquid-glass refraction switch. */
(function () {
  "use strict";
  // SVG refraction in backdrop-filter renders correctly only in Chromium (WebView2 on Windows, Linux Qt/GTK may differ);
  // everywhere else the plain blur from styles.css is used.
  try {
    const chromium = !!window.chrome || /Chrome\/|Edg\//.test(navigator.userAgent);
    const reduced = matchMedia("(prefers-reduced-transparency: reduce)").matches;
    if (chromium && !reduced) document.documentElement.classList.add("liquid");
  } catch (e) { /* ignore */ }

  const reduceMotion = () => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; } };
  document.addEventListener("pointerdown", ev => {
    if (ev.button !== 0 || reduceMotion()) return;
    const host = ev.target.closest && ev.target.closest(".btn, nav button, .choice button");
    if (!host || host.disabled) return;
    const r = host.getBoundingClientRect();
    const size = Math.max(r.width, r.height) * 2.2;
    const s = document.createElement("span");
    s.className = "rip";
    s.style.width = s.style.height = size + "px";
    s.style.left = (ev.clientX - r.left - size / 2) + "px";
    s.style.top = (ev.clientY - r.top - size / 2) + "px";
    host.appendChild(s);
    s.addEventListener("animationend", () => s.remove());
    setTimeout(() => s.remove(), 900);
  }, { passive: true });
})();

/* Native feel: no browser context menu (Back / Reload / Print / Inspect) and no dragging links or images out of the window.
   Text fields and selectable text keep their menu so copy and paste still work. */
(function () {
  "use strict";
  const editable = el => el && el.closest && el.closest('input, textarea, [contenteditable], #log, .mono, .kv dd, pre, .warn .grow, .selectable');
  document.addEventListener("contextmenu", ev => {
    if (editable(ev.target) || (window.getSelection && String(window.getSelection()).length)) return;
    ev.preventDefault();
  });
  document.addEventListener("dragstart", ev => { if (!editable(ev.target)) ev.preventDefault(); });
})();
