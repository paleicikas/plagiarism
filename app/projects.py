"""Checks ("projects"): one folder per check with the PDF copy, its work folder and project.json.

projects/<id>/
  document.pdf
  project.json        metadata, options, status, last progress state
  local_sources/      user-provided source files
  work/               pipeline output and caches
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

STATUSES = ["new", "ready", "running", "paused", "interrupted", "cancelled", "failed", "done"]


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def folder(self, pid: str) -> Path:
        p = (self.root / pid).resolve()
        if p.parent != self.root.resolve():
            raise ValueError("bad project id")
        return p

    def _file(self, pid: str) -> Path:
        return self.folder(pid) / "project.json"

    def get(self, pid: str) -> dict | None:
        with self.lock:
            try:
                return json.loads(self._file(pid).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None

    def save(self, p: dict) -> dict:
        with self.lock:
            p["updated"] = datetime.now().isoformat(timespec="seconds")
            f = self._file(p["id"])
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
            for attempt in range(5):
                try:
                    tmp.replace(f)
                    break
                except PermissionError:      # Windows: the file is briefly open elsewhere (antivirus, indexer)
                    if attempt == 4:
                        raise
                    time.sleep(0.1 * (attempt + 1))
        return p

    def update(self, pid: str, **fields) -> dict:
        with self.lock:
            p = self.get(pid)
            if p is None:
                raise KeyError(pid)
            p.update(fields)
            return self.save(p)

    def create(self, pdf_path: Path, name: str | None = None) -> dict:
        pid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        d = self.folder(pid)
        (d / "work").mkdir(parents=True)
        (d / "local_sources").mkdir()
        shutil.copy2(pdf_path, d / "document.pdf")
        p = dict(id=pid, name=name or Path(pdf_path).stem, pdf_name=Path(pdf_path).name,
                 created=datetime.now().isoformat(timespec="seconds"), status="new",
                 meta={}, options={}, state=None, error=None, summaries={}, report=None)
        return self.save(p)

    def list(self) -> list[dict]:
        out = []
        for f in self.root.glob("*/project.json"):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append(dict(id=p["id"], name=p.get("name"), pdf_name=p.get("pdf_name"), created=p.get("created"),
                            updated=p.get("updated"), status=p.get("status"), meta=p.get("meta", {}),
                            risk=(p.get("result") or {}).get("risk"),
                            problem_pct=(p.get("result") or {}).get("problem_pct"),
                            percent=((p.get("state") or {}).get("percent"))))
        out.sort(key=lambda x: x.get("updated") or x.get("created") or "", reverse=True)
        return out

    def delete(self, pid: str):
        d = self.folder(pid)
        for _ in range(3):
            try:
                shutil.rmtree(d)
                return
            except OSError:
                time.sleep(0.5)          # Windows: sqlite files may still be closing
        shutil.rmtree(d, ignore_errors=True)

    def mark_interrupted(self):
        """At start-up: runs that were going on when the app closed can be resumed."""
        for f in self.root.glob("*/project.json"):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if p.get("status") in ("running", "paused"):
                p["status"] = "interrupted"
                self.save(p)

    def pdf(self, pid: str) -> Path:
        return self.folder(pid) / "document.pdf"

    def work(self, pid: str) -> Path:
        return self.folder(pid) / "work"

    def local_sources(self, pid: str) -> Path:
        d = self.folder(pid) / "local_sources"
        d.mkdir(exist_ok=True)
        return d
