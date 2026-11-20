"""Keeps the computer from going to sleep while a check is running."""
from __future__ import annotations

import shutil
import subprocess
import sys


class KeepAwake:
    def __init__(self):
        self.proc = None
        self.active = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *a):
        self.stop()

    def start(self):
        if self.active:
            return
        try:
            if sys.platform == "win32":
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            elif sys.platform == "darwin" and shutil.which("caffeinate"):
                self.proc = subprocess.Popen(["caffeinate", "-i"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif shutil.which("systemd-inhibit"):
                self.proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep", "--who=Plagiarism", "--why=Plagiarism check running",
                     "--mode=block", "sleep", "infinity"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.active = True
        except Exception:
            self.proc = None

    def stop(self):
        if not self.active:
            return
        self.active = False
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass
        self.proc = None
