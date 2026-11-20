"""Methods callable from the UI (`window.pywebview.api.<name>(...)`). Every method returns JSON-serialisable data;
errors are returned as {error: {code, params, message}} so the UI can translate them."""
from __future__ import annotations

import base64
import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

from plagcheck import __author__, __version__, i18n, pipeline
from plagcheck.errors import PipelineError

from .projects import ProjectStore
from .runner import Runner
from .settings import SECRETS, Settings, data_dir, test_key

UI_SECTIONS = ["_meta", "app", "step", "phase", "cur", "counter", "warning", "error", "risk", "verdict", "auto"]
LOCAL_SOURCE_TYPES = {".txt", ".pdf", ".html", ".htm", ".md"}
MAX_UPLOAD = 300 * 1024 * 1024
REPO = "paleicikas/plagiarism"


def _err(e: Exception) -> dict:
    if isinstance(e, PipelineError):
        return dict(error=e.to_dict())
    return dict(error=dict(code="unexpected", params=dict(detail=f"{type(e).__name__}: {e}"), message=str(e),
                           traceback=traceback.format_exc()[-3000:]))


def safe(fn):
    @functools.wraps(fn)
    def wrap(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            return _err(e)
    return wrap


def open_in_os(path: str):
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Api:
    def __init__(self, settings: Settings | None = None, store: ProjectStore | None = None):
        self._settings = settings or Settings()
        self._store = store or ProjectStore(data_dir() / "projects")
        self._window = None
        self._lock = threading.Lock()
        self._runner = Runner(self._store, self._settings, self._push,
                              texts=lambda k, **p: i18n.t(k, self._settings.language, **p))

    # ------------------------------------------------------------ plumbing
    def _push(self, events: list, state: dict | None):
        if self._window is None:
            return
        payload = json.dumps(dict(events=events, state=state), ensure_ascii=False, default=str)
        self._window.evaluate_js(f"window.__plagcheckEvents && window.__plagcheckEvents({payload})")

    def _dialog(self, kind, **kw):
        import webview
        if self._window is None:
            return None
        return self._window.create_file_dialog(getattr(webview.FileDialog, kind), **kw)

    def _project(self, pid: str) -> dict:
        p = self._store.get(pid)
        if p is None:
            raise PipelineError("project.missing", "Check not found.")
        return p

    def _config(self, p: dict, extra: dict | None = None):
        pid = p["id"]
        opts = dict(p.get("options") or {}, **(extra or {}))
        cfg = self._settings.build_config(self._store.work(pid), self._store.pdf(pid), p.get("meta") or {}, opts)
        if p.get("demo"):
            cfg.search_backend, cfg.llm_provider, cfg.use_wayback = "none", "none", False
        elif opts.get("llm_off"):
            cfg.llm_provider = "none"
        return cfg

    # ------------------------------------------------------------ start-up / settings
    @safe
    def init(self) -> dict:
        s = self._settings
        return dict(version=__version__, author=__author__, platform=sys.platform, machine=platform.machine(), repo=REPO,
                    settings=s.public(), languages=i18n.languages(), locales=i18n.bundle(UI_SECTIONS),
                    projects=self._store.list(), active=self._runner.active_pid(),
                    steps=pipeline.STEPS, weights=pipeline.WEIGHTS, data_dir=str(self._store.root))

    @safe
    def get_settings(self) -> dict:
        return self._settings.public()

    @safe
    def save_settings(self, values: dict) -> dict:
        self._settings.update(values)
        return self._settings.public()

    @safe
    def set_key(self, name: str, value: str) -> dict:
        if name not in SECRETS:
            raise PipelineError("unexpected", detail=f"unknown key {name}")
        self._settings.secrets.set(name, value)
        return self._settings.public()

    @safe
    def test_key(self, name: str, value: str = "") -> dict:
        if name in SECRETS and not value:
            value = self._settings.secrets.get(name)
        return test_key(name, value, base_url=self._settings["openai_base_url"] or "")

    # ------------------------------------------------------------ projects
    @safe
    def list_projects(self) -> list:
        return self._store.list()

    @safe
    def get_project(self, pid: str) -> dict:
        p = self._project(pid)
        p["active"] = self._runner.active_pid() == pid
        p["local_sources"] = sorted(f.name for f in self._store.local_sources(pid).iterdir() if f.is_file())
        p["has_report"] = (self._store.work(pid) / "verdicts.json").exists()
        return p

    @safe
    def pick_pdf(self) -> dict:
        files = self._dialog("OPEN", file_types=("PDF (*.pdf)",))
        if not files:
            return dict(cancelled=True)
        return self._create(Path(files[0] if isinstance(files, (list, tuple)) else files))

    @safe
    def create_from_path(self, path: str) -> dict:
        return self._create(Path(path))

    @safe
    def create_from_data(self, name: str, b64: str) -> dict:
        data = base64.b64decode(b64.split(",", 1)[-1])
        if len(data) > MAX_UPLOAD:
            raise PipelineError("pdf.too_large", "PDF is too large.")
        tmp = self._store.root / f"_upload_{os.getpid()}_{threading.get_ident()}.pdf"
        tmp.write_bytes(data)
        try:
            return self._create(tmp, name=Path(name).stem)
        finally:
            tmp.unlink(missing_ok=True)

    def _create(self, pdf: Path, name: str | None = None) -> dict:
        if pdf.suffix.lower() != ".pdf" or not pdf.is_file():
            raise PipelineError("pdf.not_pdf", "Not a PDF file.")
        from plagcheck.extract import inspect_pdf
        meta = inspect_pdf(pdf)                      # raises a translated error for broken / encrypted PDFs
        p = self._store.create(pdf, name=name)
        p["meta"] = meta
        p["name"] = meta.get("title") or p["name"]
        p["options"] = dict(report_lang=self._settings.report_language, include_refs=bool(self._settings["include_refs"]))
        p["status"] = "ready"
        self._store.save(p)
        return self.get_project(p["id"])

    @safe
    def create_demo(self) -> dict:
        from plagcheck import demo
        tmp = self._store.root / f"_demo_{os.getpid()}"
        tmp.mkdir(exist_ok=True)
        pdf = tmp / "demo-thesis.pdf"
        demo.make_pdf(pdf)
        try:
            p = self._create(pdf, name="Demo")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        demo.write_sources(self._store.local_sources(p["id"]))
        p = self._project(p["id"])
        p.update(demo=True, name=p["meta"].get("title") or "Demo")
        p["meta"].update(demo.META)
        self._store.save(p)
        return self.get_project(p["id"])

    @safe
    def update_project(self, pid: str, fields: dict) -> dict:
        p = self._project(pid)
        for k in ("name", "meta", "options"):
            if k in fields and fields[k] is not None:
                p[k] = dict(p.get(k) or {}, **fields[k]) if isinstance(fields[k], dict) else fields[k]
        self._store.save(p)
        return self.get_project(pid)

    @safe
    def delete_project(self, pid: str) -> dict:
        if self._runner.active_pid() == pid:
            raise PipelineError("busy", "The check is running.")
        self._store.delete(pid)
        return dict(ok=True)

    @safe
    def add_local_sources(self, pid: str) -> dict:
        files = self._dialog("OPEN", allow_multiple=True,
                             file_types=("Text, PDF, HTML (*.txt;*.pdf;*.html;*.htm;*.md)", "All files (*.*)"))
        dest = self._store.local_sources(pid)
        for f in files or []:
            f = Path(f)
            if f.suffix.lower() in LOCAL_SOURCE_TYPES and f.is_file():
                shutil.copy2(f, dest / f.name)
        return self.get_project(pid)

    @safe
    def remove_local_source(self, pid: str, name: str) -> dict:
        f = self._store.local_sources(pid) / Path(name).name
        f.unlink(missing_ok=True)
        return self.get_project(pid)

    # ------------------------------------------------------------ estimate / run
    @safe
    def estimate(self, pid: str) -> dict:
        p = self._project(pid)
        if self._runner.active_pid() == pid:
            raise PipelineError("busy", "The check is running.")
        cfg = self._config(p)
        pipeline.run_pipeline(cfg, ["extract"])
        from plagcheck.estimate import estimate
        est = estimate(cfg, include_refs=bool((p.get("options") or {}).get("include_refs")))
        self._store.update(pid, estimate=est)
        return est

    @safe
    def start(self, pid: str) -> dict:
        p = self._project(pid)
        opts = p.get("options") or {}
        cfg = self._config(p)
        include_refs = bool(opts.get("include_refs")) and not p.get("demo")
        steps = pipeline.default_steps(include_refs)
        skip = [] if include_refs else ["refs"]
        self._runner.start(pid, cfg, steps, skip, dict(report_lang=cfg.report_lang))
        return dict(ok=True)

    @safe
    def pause(self) -> dict:
        self._runner.pause()
        return dict(ok=True)

    @safe
    def resume(self) -> dict:
        self._runner.resume()
        return dict(ok=True)

    @safe
    def cancel(self) -> dict:
        self._runner.cancel()
        return dict(ok=True)

    @safe
    def run_state(self, pid: str, with_log: bool = True) -> dict:
        st = self._runner.state
        if st is not None and st.pid == pid:
            return st.snapshot(with_log=with_log)
        p = self._project(pid)
        log = []
        if with_log:
            try:
                log = json.loads((self._store.folder(pid) / "log.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return dict(p.get("state") or {}, pid=pid, status=p.get("status"), error=p.get("error"), log=log)

    # ------------------------------------------------------------ report / export
    @safe
    def report_html(self, pid: str, lang: str = "") -> dict:
        from plagcheck import report
        p = self._project(pid)
        cfg = self._config(p)
        data = report.build_data(cfg, lang or None)
        data["force_lang"] = data["embedded"] = True
        return dict(html=report.render(data))

    @safe
    def export(self, pid: str, fmt: str, lang: str = "") -> dict:
        from plagcheck import export
        p = self._project(pid)
        base = re.sub(r'[\\/:*?"<>|]+', "_", p.get("name") or "report").strip()[:80] or "report"
        suffix = "" if (lang or "en") == "en" else f"_{lang}"
        res = self._dialog("SAVE", save_filename=f"{base}{suffix}.{fmt}",
                           file_types=(f"{fmt.upper()} (*.{fmt})",))
        if not res:
            return dict(cancelled=True)
        out = Path(res[0] if isinstance(res, (list, tuple)) else res)
        if out.suffix.lower() != f".{fmt}":
            out = out.with_suffix(f".{fmt}")
        export.export(self._config(p), fmt, out, lang or None)
        return dict(path=str(out))

    @safe
    def save_log(self, pid: str) -> dict:
        st = self.run_state(pid, True)
        lines = [f"{e.get('level', ''):7} {e.get('msg', '')}" for e in st.get("log", [])]
        res = self._dialog("SAVE", save_filename=f"plagcheck-{pid}.log", file_types=("Log (*.log;*.txt)",))
        if not res:
            return dict(cancelled=True)
        out = Path(res[0] if isinstance(res, (list, tuple)) else res)
        out.write_text("\n".join(lines), encoding="utf-8")
        return dict(path=str(out))

    # ------------------------------------------------------------ OS helpers
    @safe
    def open_path(self, path: str) -> dict:
        open_in_os(path)
        return dict(ok=True)

    @safe
    def reveal(self, path: str) -> dict:
        p = Path(path)
        if sys.platform == "win32" and p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            open_in_os(str(p if p.is_dir() else p.parent))
        return dict(ok=True)

    @safe
    def open_url(self, url: str) -> dict:
        if not re.match(r"^(https?|mailto):", url or ""):
            raise PipelineError("unexpected", detail="bad url")
        webbrowser.open(url)
        return dict(ok=True)

    @safe
    def open_report_in_browser(self, pid: str, lang: str = "") -> dict:
        from plagcheck import report
        p = self._project(pid)
        out = self._store.work(pid) / "index.html"
        report.run(self._config(p), out, lang or None)
        webbrowser.open(out.as_uri())
        return dict(ok=True)

    def _on_closing(self):
        if self._runner.busy:
            self._runner.cancel()
            pid = self._runner.active_pid()
            if self._runner.thread:
                self._runner.thread.join(timeout=5)
            if pid:
                try:
                    self._store.update(pid, status="interrupted")
                except Exception:
                    pass
