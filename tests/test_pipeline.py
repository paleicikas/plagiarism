import json
import threading

import pytest

from plagcheck import demo, progress
from plagcheck.errors import Cancelled, PipelineError
from plagcheck.estimate import estimate
from plagcheck.pipeline import run_pipeline
from plagcheck.util import read_json


def test_demo_pipeline_offline(tmp_path):
    events = []
    out = demo.run(tmp_path / "d", lang="lt", on_event=events.append)
    html = out.read_text(encoding="utf-8")
    assert "Smith_Clark_2008_review" in html
    types = [e["type"] for e in events]
    assert types[0] == "run_start" and types[-1] == "run_done"
    done = [e["step"] for e in events if e["type"] == "step_done"]
    assert done == ["extract", "search", "fetch", "align", "verify", "report"]
    assert any(e["type"] == "step_progress" and e.get("phase") == "align.compare" for e in events)
    vd = read_json(tmp_path / "d" / "work" / "verdicts.json")
    assert vd["stats"]["problem_pct"] > 10
    json.dumps(events)          # events must be JSON-serialisable for the UI bridge


def test_estimate(tmp_path):
    cfg = demo.build(tmp_path / "e")
    run_pipeline(cfg, ["extract"])
    est = estimate(cfg)
    assert est["queries"] > 0 and est["engine"] == "none" and est["llm"] is None


def test_cancel(tmp_path):
    cfg = demo.build(tmp_path / "c")
    ctl = progress.RunControl()
    ctl.cancel()
    events = []
    with pytest.raises(Cancelled):
        run_pipeline(cfg, ["extract", "search"], on_event=events.append, control=ctl)
    assert events[-1]["type"] == "run_cancelled"


def test_pause_resume(tmp_path):
    cfg = demo.build(tmp_path / "p")
    ctl = progress.RunControl()
    ctl.pause()
    threading.Timer(0.6, ctl.resume).start()
    run_pipeline(cfg, ["extract"], control=ctl)


def test_errors_are_coded(tmp_path):
    cfg = demo.build(tmp_path / "x")
    cfg.pdf = str(tmp_path / "missing.pdf")
    with pytest.raises(PipelineError) as e:
        run_pipeline(cfg, ["extract"])
    assert e.value.code == "pdf.open_failed"
    cfg2 = demo.build(tmp_path / "y")
    with pytest.raises(PipelineError) as e:
        run_pipeline(cfg2, ["align"])
    assert e.value.code == "data.missing"
