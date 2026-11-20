"""Structured progress events, pause / cancel control.

Event types: step_start, step_progress, step_done, step_skipped, step_failed, step_cancelled, warning, log,
run_done, run_failed, run_cancelled. Every event is a dict with `type` and `ts`.

User-facing texts are not sent as ready strings: `current` and warnings carry an i18n key plus parameters,
counters are keyed by name (`counter.<name>` in the locale files).
"""
from __future__ import annotations

import sys
import threading
import time

from .errors import Cancelled

_listeners: list = []
_listeners_lock = threading.Lock()
_state = {"step": None}


def add_listener(fn):
    with _listeners_lock:
        _listeners.append(fn)


def remove_listener(fn):
    with _listeners_lock:
        if fn in _listeners:
            _listeners.remove(fn)


def emit(type_: str, **data):
    data["type"] = type_
    data["ts"] = time.time()
    if "step" not in data and _state["step"]:
        data["step"] = _state["step"]
    with _listeners_lock:
        ls = list(_listeners)
    for fn in ls:
        try:
            fn(data)
        except Exception as e:  # a broken listener must not stop the pipeline
            print(f"progress listener error: {e!r}", file=sys.stderr)


def set_step(step: str | None):
    _state["step"] = step


def current_step() -> str | None:
    return _state["step"]


# ---------------------------------------------------------------- pause / cancel

class RunControl:
    def __init__(self):
        self.cancelled = threading.Event()
        self._running = threading.Event()
        self._running.set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def pause(self):
        self._running.clear()

    def resume(self):
        self._running.set()

    def cancel(self):
        self.cancelled.set()
        self._running.set()

    def checkpoint(self):
        if self.cancelled.is_set():
            raise Cancelled()
        if not self._running.is_set():
            emit("paused")
            while not self._running.wait(0.5):
                pass
            if self.cancelled.is_set():
                raise Cancelled()
            emit("resumed")


_control = RunControl()


def set_control(c: RunControl | None):
    global _control
    _control = c or RunControl()


def control() -> RunControl:
    return _control


def checkpoint():
    _control.checkpoint()


# ---------------------------------------------------------------- helpers used by the steps

def warn(key: str, message: str = "", **params):
    """A warning shown in the UI (translated by `key`) and printed to the log (English `message`)."""
    from .util import log
    if message:
        log("WARNING:", message, _event=False)
    emit("warning", key=key, params=params, message=message)


def activity(key: str, **params) -> dict:
    """`current` activity descriptor: an i18n key (`cur.<key>`) with parameters."""
    return dict(key=key, params=params)


class Progress:
    """Counts work inside a step. Emits throttled `step_progress` events and prints a log line every few seconds."""

    def __init__(self, total: int, label: str, phase: str | None = None, every: float = 2.0, emit_every: float = 0.2):
        self.total, self.label, self.every, self.emit_every = total, label, every, emit_every
        self.step = current_step()
        self.phase = phase or self.step
        self.done = 0
        self.t0 = self.last = self.last_emit = time.time()
        self.lock = threading.Lock()
        self.counters: dict[str, float] = {}
        self.cost: dict = {}
        self.current = None
        self._emit(force=True)

    def count(self, name: str, n: float = 1):
        with self.lock:
            self.counters[name] = self.counters.get(name, 0) + n

    def set(self, current: dict | None = None, cost: dict | None = None, counters: dict | None = None):
        with self.lock:
            if current is not None:
                self.current = current
            if cost:
                self.cost.update(cost)
            if counters:
                self.counters.update(counters)
        self._emit()

    def tick(self, n: int = 1, extra: str = "", current: dict | None = None, count: str | None = None):
        checkpoint()
        with self.lock:
            self.done += n
            if count:
                self.counters[count] = self.counters.get(count, 0) + 1
            if current is not None:
                self.current = current
            now = time.time()
            if now - self.last >= self.every or self.done >= self.total:
                self.last = now
                rate = self.done / max(now - self.t0, 1e-6)
                eta = (self.total - self.done) / rate if rate > 0 else 0
                from .util import log
                log(f"{self.label}: {self.done}/{self.total} ({rate:.1f}/s, ~{eta / 60:.1f} min left) {extra}")
        self._emit(force=self.done >= self.total)

    def _emit(self, force: bool = False):
        now = time.time()
        with self.lock:
            if not force and now - self.last_emit < self.emit_every:
                return
            self.last_emit = now
            el = now - self.t0
            rate = self.done / el if el > 0 else 0
            eta = (self.total - self.done) / rate if rate > 0 and self.done else None
            data = dict(step=self.step, phase=self.phase, done=self.done, total=self.total,
                        rate=round(rate, 3), eta_s=round(eta) if eta is not None else None, elapsed_s=round(el),
                        current=self.current, counters=dict(self.counters), cost=dict(self.cost))
        emit("step_progress", **data)
