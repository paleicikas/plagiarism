"""Common helper functions: text normalisation, sentence splitting, caching."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# ---------------------------------------------------------------- logging

def log(*args, _event: bool = True, **kw):
    ts = time.strftime("%H:%M:%S")
    msg = " ".join(str(a) for a in args)
    try:
        print(f"[{ts}]", msg, **kw, file=sys.stderr, flush=True)
    except (AttributeError, OSError, ValueError):   # windowed app without a console
        pass
    if not _event:
        return
    from .progress import emit
    low = msg[:12].lower()
    level = "error" if low.startswith("error") else "warning" if low.startswith("warning") else "info"
    emit("log", level=level, message=msg)


from .progress import Progress  # noqa: E402  (re-exported for the step modules)


# ---------------------------------------------------------------- JSON I/O

def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data, indent: int | None = 1):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    tmp.replace(path)


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- KV cache (sqlite, thread-safe)

class KVCache:
    """Simple key→JSON cache. Allows interrupting and resuming work without paying again."""

    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=60)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts REAL)")
        self.conn.commit()
        self.lock = threading.Lock()

    def get(self, key: str):
        with self.lock:
            row = self.conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, value):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)",
                              (key, json.dumps(value, ensure_ascii=False), time.time()))
            self.conn.commit()

    def __contains__(self, key):
        with self.lock:
            return self.conn.execute("SELECT 1 FROM kv WHERE k=?", (key,)).fetchone() is not None


# ---------------------------------------------------------------- text normalisation

_QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
           "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
           "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00ad": "", "\u00a0": " ",
           "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
_QUOTE_RE = re.compile("|".join(map(re.escape, _QUOTES)))
_WORD_RE = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*", re.UNICODE)


def clean_text(s: str) -> str:
    """Normalises quotes, dashes, ligatures and whitespace (for display and search)."""
    s = unicodedata.normalize("NFKC", s)
    s = _QUOTE_RE.sub(lambda m: _QUOTES[m.group(0)], s)
    s = re.sub(r"[\t\r\f\v​﻿]", " ", s)
    s = re.sub(r"[ ]{2,}", " ", s)
    return s.strip()


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokens(s: str) -> list[str]:
    """Lowercase words without diacritics (for comparison)."""
    s = strip_accents(clean_text(s)).lower()
    return [w.replace("'", "").replace("-", "") for w in _WORD_RE.findall(s)]


def word_count(s: str) -> int:
    return len(_WORD_RE.findall(s))


STOPWORDS = set("""a about above after again against all also am an and any are aren't as at be because been
before being below between both but by can cannot could did do does doing down during each either et al etc
few for from further had has have having he her here hers herself him himself his how however i if in into is
it its itself just less let like may me might more most much must my myself no nor not now of off on once only
or other others otherwise our ours ourselves out over own per rather same she should since so some such than
that the their theirs them themselves then there therefore these they this those though through thus to too
under until up upon us very via was we were what when where whether which while who whom whose why will with
within without would yet you your yours yourself yourselves e g i e eg ie one two also may well even""".split())


def content_tokens(toks: list[str]) -> list[str]:
    return [t for t in toks if t not in STOPWORDS and len(t) > 2 and not t.isdigit()]


def ngrams(toks: list[str], n: int) -> list[tuple]:
    return [tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)]


# ---------------------------------------------------------------- sentence splitting

_ABBR = ["et al", "et", "al", "e.g", "i.e", "eg", "ie", "etc", "vs", "cf", "pp", "p", "no", "nos", "vol", "fig",
         "figs", "eq", "ch", "ed", "eds", "dr", "prof", "mr", "mrs", "ms", "st", "jr", "sr", "inc", "ltd", "co",
         "dept", "univ", "approx", "ca", "resp", "viz", "op", "cit", "ibid", "n.d", "u.s", "u.k", "e.u", "jan",
         "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "tab", "sect", "app"]
_ABBR_RE = re.compile(r"\b(" + "|".join(re.escape(a) for a in sorted(_ABBR, key=len, reverse=True)) + r")\.$",
                      re.IGNORECASE)
_SPLIT_RE = re.compile(r'(?<=[.!?])["\')\]]*\s+(?=["\'(\[]?[A-Z0-9Ā-ſ])')


def split_sentences(text: str) -> list[str]:
    """Splits text into sentences, preserving abbreviations (et al., e.g., p. ...) and initials."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts, start = [], 0
    for m in _SPLIT_RE.finditer(text):
        left = text[start:m.start()].rstrip("\"')] ")
        # do not split after abbreviations or single-letter initials ("J. Smith", "A.")
        if _ABBR_RE.search(left) or re.search(r"(?:^|[\s(])[A-Z]\.$", left):
            continue
        # do not split after a number list marker like "1." at the beginning
        if re.fullmatch(r"\d{1,2}\.", left.strip()):
            continue
        parts.append(text[start:m.start()].strip())
        start = m.end()
    parts.append(text[start:].strip())
    return [p for p in parts if p]


# ---------------------------------------------------------------- URLs

_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref"}


def norm_url(url: str) -> str:
    try:
        p = urlsplit(url.strip())
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in _TRACKING]
        host = p.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = p.path.rstrip("/") or "/"
        return urlunsplit(("https", host, path, urlencode(q), ""))
    except Exception:
        return url


def domain(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
