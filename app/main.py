"""Desktop entry point: `python -m app.main` (or the packaged Plagiarism executable).

    --selftest   run the offline demo check and all exports without opening a window (used by CI)
    --debug      open the web inspector
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


def ui_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app" / "ui"
    return Path(__file__).resolve().parent / "ui"


def selftest() -> int:
    """Exit code 0 = OK. The windowed Windows build has no console, so the result is also written to the file
    named by PLAGCHECK_SELFTEST_LOG (if set)."""
    import traceback
    try:
        msg, code = _selftest(), 0
    except BaseException:
        msg, code = traceback.format_exc(), 1
    out = os.environ.get("PLAGCHECK_SELFTEST_LOG")
    if out:
        Path(out).write_text(msg, encoding="utf-8")
    print(msg)
    return code


def _selftest() -> str:
    from plagcheck import __version__, demo, export, i18n

    folder = Path(tempfile.mkdtemp(prefix="plagcheck-selftest-"))
    events = []
    out = demo.run(folder, lang="en", on_event=events.append)
    assert out.exists() and out.stat().st_size > 20_000, "report not written"
    assert events[-1]["type"] == "run_done", events[-1]
    cfg = demo.build(folder)
    for lang in i18n.codes():
        for fmt in export.FORMATS:
            p = export.export(cfg, fmt, folder / f"selftest_{lang}.{fmt}", lang)
            assert p.stat().st_size > 500, f"{fmt} export too small"
    assert (ui_dir() / "index.html").exists(), "UI files missing"
    import webview  # noqa: F401  – the GUI toolkit must be bundled
    from app.settings import Settings
    Settings(folder / "cfg")
    shutil.rmtree(folder, ignore_errors=True)
    return f"Plagiarism {__version__} selftest OK"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()
    if "--version" in argv:
        from plagcheck import __version__
        print(__version__)
        return 0

    import webview
    from plagcheck import __version__, i18n

    from app.api import Api
    from app.projects import ProjectStore
    from app.settings import Settings, data_dir

    settings = Settings()
    store = ProjectStore(data_dir() / "projects")
    store.mark_interrupted()
    api = Api(settings, store)
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_DOWNLOADS"] = False
    window = webview.create_window(f"{i18n.t('app.name', settings.language)} {__version__}", url=(ui_dir() / "index.html").as_uri(), js_api=api,
                                   width=1320, height=880, min_size=(980, 660), text_select=True,
                                   background_color="#f6f5f0")
    api._window = window
    window.events.closing += api._on_closing
    debug = "--debug" in argv or os.environ.get("PLAGCHECK_DEBUG") == "1"
    # WinForms loads the window icon with System.Drawing.Icon, which only accepts .ico files (a PNG crashes at startup).
    icon = ui_dir() / ("icon.ico" if sys.platform == "win32" else "icon.png")
    gui = "qt" if sys.platform.startswith("linux") and getattr(sys, "frozen", False) else None
    webview.start(debug=debug, private_mode=False, storage_path=str(data_dir() / "webview"),
                  icon=str(icon) if icon.exists() else None, gui=gui)
    return 0


if __name__ == "__main__":
    sys.exit(main())
