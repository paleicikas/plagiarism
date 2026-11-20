import json
import re
from pathlib import Path

import pytest

from plagcheck import i18n

ROOT = Path(__file__).resolve().parents[1]
LOCALE_DIRS = [ROOT / "plagcheck" / "locales", ROOT / "site" / "locales"]


def _keys(path: Path) -> dict:
    return i18n.flatten(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("folder", LOCALE_DIRS, ids=lambda p: p.parent.name)
def test_all_languages_have_the_same_keys(folder):
    files = sorted(folder.glob("*.json"))
    assert files, f"no locale files in {folder}"
    base = _keys(folder / "en.json")
    for f in files:
        keys = _keys(f)
        missing = sorted(set(base) - set(keys))
        extra = sorted(set(keys) - set(base))
        assert not missing, f"{f.name} is missing: {missing[:20]}"
        assert not extra, f"{f.name} has keys not in en.json: {extra[:20]}"


@pytest.mark.parametrize("folder", LOCALE_DIRS, ids=lambda p: p.parent.name)
def test_placeholders_match(folder):
    base = _keys(folder / "en.json")
    ph = re.compile(r"\{(\w+)\}")
    for f in sorted(folder.glob("*.json")):
        for k, v in _keys(f).items():
            if k in base and isinstance(v, str):
                assert set(ph.findall(v)) == set(ph.findall(base[k])), f"{f.name}: placeholders differ in {k}"


def test_engine_keys_exist():
    """Keys referenced from Python code (errors, warnings, activities, counters) exist in en.json."""
    en = _keys(ROOT / "plagcheck" / "locales" / "en.json")
    files = list((ROOT / "plagcheck").glob("*.py")) + list((ROOT / "app").glob("*.py"))
    src = "\n".join(p.read_text(encoding="utf-8") for p in files)
    for code in re.findall(r'PipelineError\(\s*"([\w.]+)"', src):
        assert f"error.{code}" in en, f"error.{code}"
    for key in re.findall(r'\bwarn\(\s*"([\w.]+)"', src):
        assert f"warning.{key}" in en, f"warning.{key}"
    for key in re.findall(r'\bactivity\(\s*"([\w.]+)"', src):
        assert f"cur.{key}" in en, f"cur.{key}"
    for key in re.findall(r'phase="([\w.]+)"', src):
        assert f"phase.{key}" in en, f"phase.{key}"


def test_ui_keys_exist():
    """Literal keys used by the desktop UI exist in en.json (dynamic keys ending in '.' or '_' are skipped)."""
    en = _keys(ROOT / "plagcheck" / "locales" / "en.json")
    js = (ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'\bt\("([\w.]+)"', js)) | set(re.findall(r'data-t="([\w.]+)"', html))
    missing = sorted(k for k in keys if not k.endswith((".", "_")) and k not in en)
    assert not missing, missing


def test_site_keys_exist():
    """Keys used by the website (index.html attributes and t("...") calls in app.js) exist in site/locales/en.json."""
    en = _keys(ROOT / "site" / "locales" / "en.json")
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-th?="([\w.]+)"', html))
    for spec in re.findall(r'data-ta="([^"]+)"', html):
        keys |= {pair.split(":")[1] for pair in spec.split(",")}
    keys |= {k for k in re.findall(r'\bt\("([\w.]+)"', js) if not k.endswith(".")}
    missing = sorted(k for k in keys if k not in en)
    assert not missing, missing
    js_langs = re.search(r"var LANGS = \[([^\]]*)\]", js).group(1)
    assert sorted(re.findall(r'"(\w+)"', js_langs)) == sorted(p.stem for p in (ROOT / "site" / "locales").glob("*.json"))


def test_t_formats_and_falls_back():
    assert i18n.t("report.page", "lt", n=5) == "Puslapis 5"
    assert i18n.t("report.page", "xx", n=5) == "Page 5"
    assert i18n.t("no.such.key", "en") == "no.such.key"
    assert {"en", "lt"} <= set(i18n.codes())
