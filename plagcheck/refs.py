"""Additional stage: ALL sources from the dissertation's reference list.

For each entry, OpenAlex is searched by title (fallback: Crossref + Unpaywall) to obtain
the DOI and an open-access PDF link. Found sources are added to the candidates (no Serper
credits used), then downloaded and compared against the entire dissertation.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

from .config import Config
from .fetch import _get, unpaywall_pdf
from .progress import checkpoint, activity
from .util import KVCache, Progress, log, read_json, write_json, tokens

CACHE_V = "v2|"
_LOCK = threading.Lock()
_LAST = {"openalex": 0.0, "crossref": 0.0}
STATS = Counter()


class Transient(Exception):
    """Transient error (rate limit, network) – result is not cached, will be retried later."""


def _polite_get(service: str, url: str, params: dict, min_gap: float):
    for attempt in range(5):
        with _LOCK:
            wait = _LAST[service] + min_gap - time.time()
            if wait > 0:
                time.sleep(wait)
            _LAST[service] = time.time()
        try:
            r = _get(url, 30, params=params)
        except Exception as e:
            STATS[f"{service}:{type(e).__name__}"] += 1
            time.sleep(2 * (attempt + 1))
            continue
        STATS[f"{service}:{r.status_code}"] += 1
        if r.status_code == 200:
            return r
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        return r
    raise Transient(service)


def _title_year(raw: str):
    m = re.search(r"\(\s*(\d{4})[a-z]?\s*\)\.?\s*(.+?)(?:[.?!]\s|$)", raw)
    if not m:
        return None, None
    return m.group(2).strip(), int(m.group(1))


def _ok_title(a: str, b: str) -> int:
    from rapidfuzz import fuzz
    return fuzz.ratio(" ".join(tokens(a)), " ".join(tokens(b)))


def via_openalex(title, year, email):
    params = {"search": title[:250], "per-page": 5}
    if email:
        params["mailto"] = email
    r = _polite_get("openalex", "https://api.openalex.org/works", params, 0.25)
    if r.status_code != 200:
        raise Transient(f"openalex {r.status_code}")
    best = None
    for w in r.json().get("results", []):
        sc = _ok_title(title, w.get("title") or "")
        py = w.get("publication_year")
        if sc >= 88 and (not year or not py or abs(py - year) <= 2) and (not best or sc > best[0]):
            best = (sc, w)
    if not best:
        return None
    w = best[1]
    loc = w.get("best_oa_location") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
    oa = loc.get("pdf_url") or (w.get("open_access") or {}).get("oa_url")
    return dict(title=w.get("title") or title, doi=doi, year=w.get("publication_year"), oa_url=oa,
                landing=(w.get("primary_location") or {}).get("landing_page_url"), via="openalex")


def via_crossref(title, year, email):
    params = {"query.bibliographic": title[:250], "rows": 5}
    if email:
        params["mailto"] = email
    r = _polite_get("crossref", "https://api.crossref.org/works", params, 0.3)
    if r.status_code != 200:
        raise Transient(f"crossref {r.status_code}")
    for m in r.json().get("message", {}).get("items", []):
        t = (m.get("title") or [""])[0]
        py = None
        for k in ("published-print", "published-online", "issued"):
            dp = (m.get(k) or {}).get("date-parts")
            if dp and dp[0] and dp[0][0]:
                py = dp[0][0]
                break
        if _ok_title(title, t) >= 88 and (not year or not py or abs(py - year) <= 2):
            return dict(title=t, doi=m.get("DOI"), year=py, oa_url=None, landing=None, via="crossref")
    return None


def lookup(ref: dict, email: str) -> dict:
    """Returns {} if definitely not found; raises Transient if the lookup failed due to a transient error."""
    title, year = _title_year(ref["raw"])
    if not title or len(title) < 15:
        return {}
    res = None
    errors = 0
    for fn in (via_openalex, via_crossref):
        try:
            res = fn(title, year, email)
        except Transient:
            errors += 1
            continue
        if res:
            break
    if not res:
        if errors == 2:
            raise Transient("both services unreachable")
        return {}
    if not res.get("oa_url") and res.get("doi") and email:
        res["oa_url"] = unpaywall_pdf(res["doi"], email)
    url = res.get("oa_url") or (f"https://doi.org/{res['doi']}" if res.get("doi") else None) or res.get("landing")
    if not url:
        return {}
    return dict(url=url, title=res["title"], doi=res.get("doi"), year=res.get("year"), oa=bool(res.get("oa_url")),
                via=res["via"], ref_raw=ref["raw"])


def run(cfg: Config):
    seg = read_json(cfg.path("segments.json"))
    if not seg:
        from .errors import PipelineError
        raise PipelineError("data.missing", "Run this first: python run.py extract", step="extract")
    refs = seg.get("references", [])
    cache = KVCache(cfg.path("cache_refs.sqlite"))
    out, todo = [], []
    for r in refs:
        hit = cache.get(CACHE_V + r["raw"])
        if hit is None:
            todo.append(r)
        elif hit:
            out.append(hit)
    log(f"Reference list entries: {len(refs)}; to look up (OpenAlex → Crossref): {len(todo)}")
    prog = Progress(len(todo), "Reference sources", phase="refs.lookup")
    prog.set(counters={"cached": len(refs) - len(todo), "found": 0, "not_found": 0, "errors": 0})
    failed = 0

    def work(r):
        checkpoint()
        prog.set(current=activity("refs", ref=r["raw"][:120]))
        return lookup(r, cfg.unpaywall_email)

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(work, r): r for r in todo}
        for f in as_completed(futs):
            r = futs[f]
            kind = "errors"
            try:
                res = f.result()
                cache.set(CACHE_V + r["raw"], res)
                if res:
                    out.append(res)
                kind = "found" if res else "not_found"
            except Transient:
                failed += 1            # not cached – will be retried next time
            except Exception as e:
                failed += 1
                log("Error:", repr(e)[:150])
            prog.tick(count=kind)
    write_json(cfg.path("ref_seeds.json"), out)
    log(f"Found: {len(out)} of {len(refs)} (OpenAlex {sum(o.get('via') == 'openalex' for o in out)}, "
        f"Crossref {sum(o.get('via') == 'crossref' for o in out)}; open-access PDFs: {sum(o['oa'] for o in out)}). "
        f"Request responses: {dict(STATS)}")
    if failed:
        log(f"Failed due to transient errors: {failed} – run 'python run.py refs' again to retry them.")
    return out
