"""Translations shared by the desktop UI, the HTML report and the exports.

Each language is one JSON file in `plagcheck/locales/<code>.json` (nested objects, looked up with dotted keys,
placeholders written as `{name}`). Adding a language = adding one file; `en.json` is the fallback.
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

FALLBACK = "en"


def locale_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "plagcheck" / "locales"
    return Path(__file__).resolve().parent / "locales"


@lru_cache(maxsize=None)
def load(lang: str) -> dict:
    p = locale_dir() / f"{lang}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def codes() -> list[str]:
    return sorted(p.stem for p in locale_dir().glob("*.json"))


def languages() -> list[dict]:
    out = []
    for c in codes():
        m = load(c).get("_meta", {})
        out.append(dict(code=c, name=m.get("name", c), english_name=m.get("english_name", c),
                        locale=m.get("locale", c)))
    return out


def english_name(lang: str) -> str:
    return load(lang).get("_meta", {}).get("english_name") or load(FALLBACK)["_meta"]["english_name"]


def normalize(lang: str | None) -> str:
    if not lang:
        return FALLBACK
    lang = lang.replace("_", "-").split("-")[0].lower()
    return lang if lang in codes() else FALLBACK


def _lookup(d: dict, key: str):
    cur = d
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, str) else None


def t(key: str, lang: str = FALLBACK, **params) -> str:
    s = _lookup(load(lang), key)
    if s is None:
        s = _lookup(load(FALLBACK), key)
    if s is None:
        return key
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


def bundle(sections: list[str] | None = None) -> dict:
    """All languages (optionally only the given top-level sections) for embedding into HTML."""
    out = {}
    for c in codes():
        d = load(c)
        out[c] = {k: v for k, v in d.items() if sections is None or k in sections or k == "_meta"}
    return out
