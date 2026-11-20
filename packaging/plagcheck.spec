# PyInstaller spec for the Plagiarism desktop app.  Build from the repository root:
#     pyinstaller packaging/plagcheck.spec --noconfirm
# Output: dist/Plagiarism/ (Windows, Linux) or dist/Plagiarism.app (macOS).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
ICONS = ROOT / "packaging" / "icons"
NAME = "Plagiarism"

datas = [
    (str(ROOT / "plagcheck" / "locales"), "plagcheck/locales"),
    (str(ROOT / "plagcheck" / "template.html"), "plagcheck"),
    (str(ROOT / "app" / "ui"), "app/ui"),
]
for pkg in ("trafilatura", "justext", "htmldate", "courlan", "dateparser", "webview", "certifi", "tld"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

hiddenimports = (
    collect_submodules("keyring.backends")
    + collect_submodules("ddgs")
    + {"win32": ["webview.platforms.winforms", "webview.platforms.edgechromium"],
       "darwin": ["webview.platforms.cocoa"]}.get(sys.platform, ["webview.platforms.qt"])
    + ["plagcheck.align", "plagcheck.collect", "plagcheck.extract", "plagcheck.fetch", "plagcheck.refs",
       "plagcheck.report", "plagcheck.verify", "plagcheck.export", "plagcheck.demo"]
)
excludes = ["tkinter", "matplotlib", "numpy.tests", "PyQt5", "PySide2", "PySide6", "IPython"]
if sys.platform != "linux":
    excludes += ["PyQt6", "qtpy"]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = str(ICONS / ("icon.ico" if sys.platform == "win32" else "icon.icns" if sys.platform == "darwin" else "icon.png"))
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=icon,
    upx=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)

if sys.platform == "darwin":
    import re
    version = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "plagcheck" / "_version.py").read_text()).group(1)
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=icon,
        bundle_identifier="io.github.paleicikas.plagcheck",
        version=version,
        info_plist={
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": version,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "© Artūras Paleičikas",
        },
    )
