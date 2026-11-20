"""Operating system notification when a long check finishes (best effort, never raises)."""
from __future__ import annotations

import shutil
import subprocess
import sys

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
# toasts need a registered AppUserModelID; PowerShell's is present on every Windows installation
_WIN_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def notify(title: str, message: str):
    try:
        if sys.platform == "win32":
            script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
                "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                "$x = $t.GetElementsByTagName('text');"
                f"$x.Item(0).AppendChild($t.CreateTextNode({_ps_quote(title)})) > $null;"
                f"$x.Item(1).AppendChild($t.CreateTextNode({_ps_quote(message)})) > $null;"
                "$n = [Windows.UI.Notifications.ToastNotification]::new($t);"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                f"{_ps_quote(_WIN_APP_ID)}).Show($n)"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        elif sys.platform == "darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "Plagiarism", title, message],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
