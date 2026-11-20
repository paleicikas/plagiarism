"""Runs a check in a background thread, aggregates progress events into a state the UI can render at any time,
and forwards the events to the window (throttled)."""
from __future__ import annotations

import json
import threading
import time
from collections import deque

from plagcheck import pipeline, progress
from plagcheck.errors import Cancelled, PipelineError

from .notify import notify
from .power import KeepAwake

# share of a step's progress that each of its phases covers
PHASE_SPAN = {"fetch.download": (0, .85), "fetch.metadata": (.85, 1), "align.compare": (0, .8),
              "align.wayback": (.8, 1), "verify.sources": (0, .92), "verify.final": (.92, 1)}
LOG_LINES = 4000


class RunState:
    def __init__(self, pid: str):
        self.pid = pid
        self.lock = threading.Lock()
        self.log: deque = deque(maxlen=LOG_LINES)
        self.d = dict(pid=pid, status="starting", started=time.time(), ended=None, steps={}, order=[],
                      weights={}, percent=0.0, current=None, step=None, warnings=[], error=None, cost={},
                      eta_s=None, paused=False)

    def snapshot(self, with_log: bool = False) -> dict:
        with self.lock:
            d = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                 for k, v in self.d.items()}
            d["steps"] = {k: dict(v) for k, v in self.d["steps"].items()}
            d["elapsed_s"] = round((self.d["ended"] or time.time()) - self.d["started"])
            if with_log:
                d["log"] = list(self.log)
            return d

    def _percent(self):
        d = self.d
        tot = sum(d["weights"].get(s, 1) for s in d["order"] if d["steps"][s]["status"] != "skipped") or 1
        got = 0.0
        for s in d["order"]:
            st = d["steps"][s]
            if st["status"] == "skipped":
                continue
            got += d["weights"].get(s, 1) * (1.0 if st["status"] == "done" else st.get("frac", 0.0))
        d["percent"] = round(100 * got / tot, 1)
        el = time.time() - d["started"]
        d["eta_s"] = round(el * (100 - d["percent"]) / d["percent"]) if d["percent"] >= 3 else None

    def apply(self, ev: dict):
        t = ev["type"]
        with self.lock:
            d = self.d
            if t == "run_start":
                order = [s for s in pipeline.STEPS if s in ev["steps"] or s in ev.get("skip", [])]
                order += [s for s in ev["steps"] if s not in order]
                d.update(order=order, weights=ev.get("weights", {}), status="running")
                d["steps"] = {s: dict(status="pending", frac=0.0) for s in order}
            elif t in ("step_start", "step_skipped"):
                st = d["steps"].setdefault(ev["step"], dict(frac=0.0))
                st.update(status="running" if t == "step_start" else "skipped", started=ev["ts"])
                if t == "step_start":
                    d["step"] = ev["step"]
                    d["current"] = None
            elif t == "step_progress":
                st = d["steps"].setdefault(ev["step"], dict(status="running"))
                total = ev.get("total") or 0
                f = (ev.get("done", 0) / total) if total else 0.0
                a, b = PHASE_SPAN.get(ev.get("phase"), (0, 1))
                st.update(phase=ev.get("phase"), done=ev.get("done"), total=total, rate=ev.get("rate"),
                          eta_s=ev.get("eta_s"), counters=ev.get("counters") or {}, frac=min(1.0, a + (b - a) * f))
                if ev.get("cost"):
                    st["cost"] = ev["cost"]
                    d["cost"][ev["step"]] = ev["cost"]
                if ev.get("current"):
                    d["current"] = ev["current"]
            elif t == "step_done":
                st = d["steps"].setdefault(ev["step"], {})
                st.update(status="done", frac=1.0, summary=ev.get("summary"), duration_s=ev.get("duration_s"))
            elif t in ("step_failed", "step_cancelled"):
                st = d["steps"].setdefault(ev["step"], {})
                st.update(status="failed" if t == "step_failed" else "cancelled", duration_s=ev.get("duration_s"),
                          error=ev.get("error"))
            elif t == "warning":
                d["warnings"].append(dict(key=ev.get("key"), params=ev.get("params") or {}, step=ev.get("step"),
                                          ts=ev["ts"], message=ev.get("message")))
                d["warnings"] = d["warnings"][-50:]
            elif t == "paused":
                d["paused"] = True
                d["status"] = "paused"
            elif t == "resumed":
                d["paused"] = False
                d["status"] = "running"
            elif t in ("run_done", "run_failed", "run_cancelled"):
                d["status"] = {"run_done": "done", "run_failed": "failed", "run_cancelled": "cancelled"}[t]
                d["ended"] = ev["ts"]
                d["error"] = ev.get("error")
                d["current"] = None
            if t == "log":
                self.log.append(dict(ts=ev["ts"], level=ev.get("level", "info"), msg=ev.get("message", ""),
                                     step=ev.get("step")))
            self._percent()


class Runner:
    """One check at a time. `push(events)` delivers a list of events to the UI (called from a helper thread)."""

    def __init__(self, store, settings, push, texts=None):
        self.store, self.settings, self.push = store, settings, push
        self.texts = texts or (lambda key, **p: key)
        self.thread: threading.Thread | None = None
        self.control: progress.RunControl | None = None
        self.state: RunState | None = None
        self.queue: deque = deque()
        self.qlock = threading.Lock()
        self.stop_flush = threading.Event()
        threading.Thread(target=self._flusher, daemon=True, name="ui-events").start()

    @property
    def busy(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def active_pid(self) -> str | None:
        return self.state.pid if self.state and self.busy else None

    # --- UI event forwarding: progress events are coalesced to the latest one per step
    def _on_event(self, ev: dict):
        st = self.state
        if st is None:
            return
        st.apply(ev)
        ev = dict(ev, pid=st.pid)
        with self.qlock:
            if ev["type"] == "step_progress" and self.queue and self.queue[-1]["type"] == "step_progress" \
                    and self.queue[-1].get("step") == ev.get("step"):
                self.queue[-1] = ev
            else:
                self.queue.append(ev)

    def _flusher(self):
        while not self.stop_flush.wait(0.2):
            with self.qlock:
                if not self.queue:
                    continue
                batch = list(self.queue)
                self.queue.clear()
            st = self.state
            summary = st.snapshot() if st else None
            try:
                self.push(batch, summary)
            except Exception:
                pass

    # --- control
    def start(self, pid: str, cfg, steps: list[str], skip: list[str], opts: dict):
        if self.busy:
            raise PipelineError("busy", "Another check is running.")
        self.control = progress.RunControl()
        self.state = RunState(pid)
        self.store.update(pid, status="running", error=None)
        self.thread = threading.Thread(target=self._run, args=(pid, cfg, steps, skip, opts), daemon=True,
                                       name=f"check-{pid}")
        self.thread.start()

    def pause(self):
        if self.control and self.busy:
            self.control.pause()
            self.store.update(self.state.pid, status="paused")

    def resume(self):
        if self.control and self.busy:
            self.control.resume()
            self.store.update(self.state.pid, status="running")

    def cancel(self):
        if self.control and self.busy:
            self.control.cancel()

    def _save_state(self, pid: str, **fields):
        snap = self.state.snapshot() if self.state else None
        try:
            self.store.update(pid, state=snap, **fields)
        except Exception:
            pass

    def _run(self, pid, cfg, steps, skip, opts):
        awake = KeepAwake() if self.settings["keep_awake"] else None
        if awake:
            awake.start()
        last_save = [0.0]

        def on_event(ev):
            self._on_event(ev)
            if ev["type"] in ("step_done", "step_start", "warning") or time.time() - last_save[0] > 15:
                last_save[0] = time.time()
                self._save_state(pid)

        status, error, result = "failed", None, None
        try:
            summaries = pipeline.run_pipeline(cfg, steps, on_event=on_event, control=self.control, skip=skip,
                                              yes=True, report_lang=opts.get("report_lang"))
            v = summaries.get("verify") or {}
            from plagcheck.report import RISK_ID
            result = dict(risk=RISK_ID.get(v.get("risk"), v.get("risk")), problem_pct=v.get("problem_pct"),
                          usd=v.get("usd"), model=v.get("model"),
                          matched_pct=(summaries.get("align") or {}).get("matched_pct"))
            status = "done"
        except Cancelled:
            status = "cancelled"
        except PipelineError as e:
            error = e.to_dict()
        except Exception as e:  # pragma: no cover – run_pipeline wraps everything, this is a last resort
            error = dict(code="unexpected", params=dict(detail=repr(e)), message=repr(e))
        finally:
            if awake:
                awake.stop()
        fields = dict(status=status, error=error)
        if result:
            fields["result"] = result
            fields["report"] = "work/index.html"
        self._save_state(pid, **fields)
        try:
            (self.store.folder(pid) / "log.json").write_text(json.dumps(list(self.state.log), ensure_ascii=False),
                                                             encoding="utf-8")
        except Exception:
            pass
        if self.settings["notify"] and status in ("done", "failed"):
            p = self.store.get(pid) or {}
            notify(self.texts(f"app.notify.{status}_title"), p.get("name") or "")
