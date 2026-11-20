"""Development only: serves the UI in a normal browser with the real Python API behind HTTP.

    python -m app.devserver [--port 8770]

Open http://127.0.0.1:8770/ – the page gets a `window.pywebview.api` shim that POSTs to /api/<method>,
and progress events are long-polled from /api/__events. File dialogs are not available (they return
"cancelled"); use the demo or drag & drop. Not included in the packaged app."""
from __future__ import annotations

import argparse
import json
import queue
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.api import Api

UI = Path(__file__).resolve().parent / "ui"
SHIM = """
window.pywebview = { api: new Proxy({}, { get: (_, name) => async (...args) => {
  const r = await fetch('/api/' + name, { method: 'POST', body: JSON.stringify(args) }); return r.json(); } }) };
(async function poll() {
  for (;;) { try { const r = await fetch('/api/__events'); const b = await r.json();
    for (const p of b) window.__plagcheckEvents && window.__plagcheckEvents(p); } catch (e) { await new Promise(r => setTimeout(r, 1000)); } }
})();
"""


class FakeWindow:
    def __init__(self):
        self.q: queue.Queue = queue.Queue()

    def evaluate_js(self, js: str):
        start = js.index("__plagcheckEvents(", js.index("&&")) + len("__plagcheckEvents(")
        self.q.put(json.loads(js[start:-1]))

    def create_file_dialog(self, *a, **kw):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    a = ap.parse_args()
    api = Api()
    win = FakeWindow()
    api._window = win

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(UI), **kw)

        def log_message(self, *args):
            pass

        def _json(self, obj):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/__events"):
                out = []
                try:
                    out.append(win.q.get(timeout=20))
                    while not win.q.empty():
                        out.append(win.q.get_nowait())
                except queue.Empty:
                    pass
                return self._json(out)
            if self.path in ("/", "/index.html") or self.path.startswith("/?"):
                html = (UI / "index.html").read_text(encoding="utf-8")
                html = html.replace('<script src="app.js">', f"<script>{SHIM}</script><script src=\"app.js\">")
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            return super().do_GET()

        def do_POST(self):
            name = self.path.rsplit("/", 1)[-1]
            n = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(n) or b"[]")
            fn = getattr(api, name, None)
            if name.startswith("_") or not callable(fn):
                return self._json(dict(error=dict(code="unexpected", params=dict(detail=f"no method {name}"))))
            res = fn(*args)
            return self._json(res if res is not None else dict(cancelled=True))

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"Plagiarism dev UI: http://127.0.0.1:{a.port}/")
    srv.serve_forever()


if __name__ == "__main__":
    main()
