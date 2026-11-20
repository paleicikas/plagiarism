"""Runs pipeline steps in order and reports structured progress events; shared by the CLI and the desktop app."""
from __future__ import annotations

import time
import traceback

from . import progress
from .config import Config
from .errors import Cancelled, PipelineError
from .progress import emit

STEPS = ["extract", "refs", "search", "fetch", "align", "verify", "report"]
# share of the total run time a step typically takes (for the overall progress bar)
WEIGHTS = {"extract": 3, "refs": 6, "search": 33, "fetch": 27, "align": 5, "verify": 24, "report": 2}


def default_steps(include_refs: bool = False) -> list[str]:
    return [s for s in STEPS if include_refs or s != "refs"]


def _run_step(cfg: Config, step: str, opts: dict):
    from . import align, collect, extract, fetch, refs, report, verify
    if step == "extract":
        out = extract.run(cfg)
        m = out["meta"]
        return out, dict(sentences=len(out["sentences"]), words=m["words"], pages=m["pages"],
                         body_pages=m["body_pages"], references=len(out["references"]))
    if step == "refs":
        out = refs.run(cfg)
        return out, dict(found=len(out))
    if step == "search":
        out = collect.run(cfg, limit=opts.get("limit"), yes=opts.get("yes", False), confirm=opts.get("confirm"))
        return out, dict(out or {})
    if step == "reaggregate":
        from .search import BACKENDS
        from .util import KVCache, read_json
        cands = collect.aggregate(cfg, read_json(cfg.path("segments.json")), read_json(cfg.path("queries.json"), []),
                                  KVCache(cfg.path("cache_search.sqlite")), [n for n in BACKENDS if n != "none"])
        return cands, dict(candidates=len(cands), selected=sum(c["selected"] for c in cands))
    if step == "fetch":
        out = fetch.run(cfg)
        st: dict = {}
        for s in out:
            st[s["status"]] = st.get(s["status"], 0) + 1
        return out, dict(sources=len(out), **st)
    if step == "align":
        out = align.run(cfg)
        return out, dict(relevant=out["stats"]["relevant_sources"], matched_pct=out["stats"]["matched_pct"],
                         sources=len(out["sources"]))
    if step == "verify":
        out = verify.run(cfg, only_top=opts.get("only_top"))
        return out, dict(risk=out["final"]["risk_level"], problem_pct=out["stats"]["problem_pct"],
                         usage=out.get("usage"), usd=out.get("usd"), model=out.get("model"))
    if step == "report":
        p = report.run(cfg, opts.get("out"), lang=opts.get("report_lang"))
        return p, dict(path=str(p), kb=p.stat().st_size // 1024)
    raise PipelineError("unknown_step", f"Unknown step: {step}", step=step)


def run_pipeline(cfg: Config, steps: list[str], on_event=None, control: progress.RunControl | None = None,
                 skip: list[str] | None = None, **opts) -> dict:
    """Runs `steps`; `skip` lists steps shown as skipped (e.g. optional refs). Raises PipelineError / Cancelled.
    Returns {step: summary}."""
    if on_event:
        progress.add_listener(on_event)
    progress.set_control(control)
    summaries: dict = {}
    t_run = time.time()
    try:
        emit("run_start", steps=steps, weights={s: WEIGHTS.get(s, 1) for s in steps}, skip=skip or [])
        for s in skip or []:
            emit("step_skipped", step=s)
        for st in steps:
            progress.checkpoint()
            progress.set_step(st)
            emit("step_start", step=st)
            t0 = time.time()
            try:
                _, summary = _run_step(cfg, st, opts)
            except Cancelled:
                emit("step_cancelled", step=st, duration_s=round(time.time() - t0))
                raise
            except PipelineError as e:
                emit("step_failed", step=st, error=e.to_dict(), duration_s=round(time.time() - t0))
                raise
            except Exception as e:
                tb = traceback.format_exc()
                err = PipelineError("unexpected", f"{type(e).__name__}: {e}", detail=f"{type(e).__name__}: {e}")
                emit("step_failed", step=st, error=dict(err.to_dict(), traceback=tb[-4000:]),
                     duration_s=round(time.time() - t0))
                raise err from e
            summaries[st] = summary
            emit("step_done", step=st, summary=_jsonable(summary), duration_s=round(time.time() - t0))
        emit("run_done", summaries=_jsonable(summaries), duration_s=round(time.time() - t_run))
        return summaries
    except Cancelled:
        emit("run_cancelled", duration_s=round(time.time() - t_run))
        raise
    except PipelineError as e:
        emit("run_failed", error=e.to_dict(), duration_s=round(time.time() - t_run))
        raise
    finally:
        progress.set_step(None)
        progress.set_control(None)
        if on_event:
            progress.remove_listener(on_event)


def _jsonable(o):
    import json
    try:
        json.dumps(o)
        return o
    except TypeError:
        return json.loads(json.dumps(o, default=str))
