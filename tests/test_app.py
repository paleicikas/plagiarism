import json
import time

import pytest

from app import settings as settings_mod
from app.api import Api
from app.projects import ProjectStore
from app.settings import Settings


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod.SecretStore, "__init__", _file_store_init)
    s = Settings(tmp_path / "cfg")
    s.update(dict(notify=False, keep_awake=False))
    a = Api(s, ProjectStore(tmp_path / "data" / "projects"))
    pushed = []
    a._runner.push = lambda events, state: pushed.append((events, state))
    a._pushed = pushed
    return a


def _file_store_init(self, folder):
    import threading
    self.file = folder / "secrets.json"
    self.lock = threading.Lock()
    self.backend = "file"


def wait(api, pid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not api._runner.busy:
            return api.get_project(pid)
        time.sleep(0.1)
    raise TimeoutError


def test_init_and_settings(api):
    b = api.init()
    assert {"en", "lt"} <= {lang["code"] for lang in b["languages"]}
    assert b["settings"]["effective_language"] == "en"
    assert "app" in b["locales"]["lt"]
    s = api.save_settings({"language": "lt", "not_a_setting": 1})
    assert s["effective_language"] == "lt" and "not_a_setting" not in s
    s = api.set_key("serper", " abc ")
    assert s["keys"]["serper"] is True and s["search_chain"][0] == "serper"
    assert api._settings.secrets.get("serper") == "abc"
    assert "abc" not in json.dumps(s)                   # keys never go back to the UI
    s = api.set_key("serper", "")
    assert s["keys"]["serper"] is False
    assert api.test_key("unpaywall", "bad")["ok"] is False
    assert api.test_key("serper", "")["code"] == "empty"


def test_demo_check_end_to_end(api, tmp_path):
    p = api.create_demo()
    pid = p["id"]
    assert p["status"] == "ready" and len(p["local_sources"]) == 3
    est = api.estimate(pid)
    assert est["sentences"] > 10 and est["engine"] == "none"
    assert api.start(pid) == {"ok": True}
    p = wait(api, pid)
    assert p["status"] == "done", p.get("error")
    assert p["result"]["risk"] == "high"
    st = api.run_state(pid)
    assert st["percent"] == 100 and st["log"]
    time.sleep(0.5)                                      # events reach the UI in batches every 0.2 s
    events = [e for batch, _ in api._pushed for e in batch]
    assert any(e["type"] == "run_done" for e in events)
    html = api.report_html(pid, "lt")["html"]
    assert '"force_lang": true' in html and "Smith_Clark_2008_review" in html
    assert api.list_projects()[0]["risk"] == "high"
    assert api.delete_project(pid) == {"ok": True}
    assert api.list_projects() == []


def test_pause_and_cancel(api):
    pid = api.create_demo()["id"]
    api.start(pid)
    api.pause()
    time.sleep(0.5)
    assert api._runner.busy
    api.cancel()
    p = wait(api, pid)
    assert p["status"] == "cancelled"
    api.start(pid)                                       # resume = run again, caches make it continue
    assert wait(api, pid)["status"] == "done"


def test_errors_are_returned_not_raised(api, tmp_path):
    r = api.create_from_path(str(tmp_path / "nope.pdf"))
    assert r["error"]["code"] == "pdf.not_pdf"
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert api.create_from_path(str(bad))["error"]["code"] == "pdf.open_failed"
    assert api.get_project("missing")["error"]["code"] == "project.missing"


def test_interrupted_runs_are_marked(tmp_path):
    store = ProjectStore(tmp_path)
    from plagcheck import demo
    pdf = tmp_path / "x.pdf"
    demo.make_pdf(pdf)
    p = store.create(pdf)
    store.update(p["id"], status="running")
    store.mark_interrupted()
    assert store.get(p["id"])["status"] == "interrupted"


def test_window_icon_is_a_real_ico():
    """WinForms (Windows) builds the window icon with System.Drawing.Icon, which rejects anything but an ICO."""
    import struct
    from app.main import ui_dir
    data = (ui_dir() / "icon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1) and count > 0
    sizes = {data[6 + 16 * i] or 256 for i in range(count)}
    assert {16, 32, 48, 256} <= sizes
