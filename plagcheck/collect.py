"""Stage 1a: query building, search, collecting and ranking candidate sources."""
from __future__ import annotations

import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

from .config import Config
import threading

from .errors import PipelineError
from .progress import checkpoint, activity, warn
from .search import BACKENDS, CreditsExhausted, SearchError, backend_names, get_backends
from .util import (KVCache, Progress, clean_text, domain, log, norm_url, read_json, sha1, tokens,
                   write_json)

DEFAULT_EXCLUDE = {"facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "youtube.com",
                   "pinterest.com", "linkedin.com", "m.facebook.com", "threads.net", "vk.com"}

SNIPPET_META_RE = re.compile(r"^\s*(?:by\s+[^·\-]{1,80}?\s*·\s*)?((?:19|20)\d{2})\s*(?:·\s*Cited by\s*\d+\s*)?(?:·|-|—)\s*")
SNIPPET_DATE_RE = re.compile(r"^\s*([A-Z][a-z]{2} \d{1,2}, (?:19|20)\d{2})\s*(?:·|-|—)\s*")


def split_snippet_meta(snippet: str) -> tuple[str, str | None]:
    """Google snippets start with "by A Author · 2009 · Cited by 12 - ..." or "Feb 28, 2012 — ...":
    take the year as the date and strip the prefix before comparison."""
    m = SNIPPET_META_RE.match(snippet)
    if m:
        return snippet[m.end():], m.group(1)
    m = SNIPPET_DATE_RE.match(snippet)
    if m:
        return snippet[m.end():], m.group(1)
    return snippet, None


PAREN_CITE_RE = re.compile(r"\((?:[^()]*?\b(?:19|20)\d{2}[a-z]?|[^()]*?n\.d\.)[^()]*\)")


def query_text(s: str) -> str:
    s = PAREN_CITE_RE.sub(" ", s)
    s = s.replace('"', " ").replace("“", " ").replace("”", " ")
    return re.sub(r"\s+", " ", s).strip()


def build_queries(cfg: Config, sentences: list[dict]) -> list[dict]:
    out = []
    mx = cfg.query_max_words
    for s in sentences:
        if not s["search"]:
            continue
        words = query_text(s["text"]).split()
        if len(words) < cfg.query_min_words:
            continue
        windows = []
        if len(words) <= mx + 4:
            windows = [words[:mx + 2]]
        else:
            step = mx - 8
            i = 0
            while i < len(words):
                w = words[i:i + mx]
                if len(w) < mx and i > 0:
                    w = words[-mx:]
                windows.append(w)
                if i + mx >= len(words):
                    break
                i += step
        for wi, w in enumerate(windows):
            q = " ".join(w)
            modes = ["loose", "exact"] if cfg.query_mode == "both" else [cfg.query_mode]
            for m in modes:
                qq = f'"{q}"' if m == "exact" else q
                out.append(dict(qid=len(out), sid=s["id"], win=wi, mode=m, q=qq, plain=q))
    return out


def snippet_match(query: str, snippet: str) -> dict:
    """How many query (sentence) words are found in the snippet, and the longest common word sequence."""
    qt = tokens(query)
    st = tokens(re.sub(r"\.{3}|…", " ", snippet))
    if not qt or not st:
        return dict(cov=0.0, lcs=0)
    sm = SequenceMatcher(None, qt, st, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 3]
    matched = sum(b.size for b in blocks)
    lcs = max((b.size for b in blocks), default=0)
    # snippet is shorter than query sometimes -> normalise by min length
    denom = max(min(len(qt), len(st)), 1)
    return dict(cov=round(min(matched / denom, 1.0), 3), lcs=lcs)


def _key(backend_name: str, cfg: Config, q: str) -> str:
    return f"{backend_name}|{cfg.search_gl}|{q}"


def cached_results(cache: KVCache, cfg: Config, names: list[str], q: str):
    for n in names:
        r = cache.get(_key(n, cfg, q))
        if r is not None:
            return r
    return None


def plan_queries(cfg: Config, limit: int | None = None) -> dict:
    """Queries for the current segments: all of them, those not cached yet and those that will run now
    (SEARCH_BUDGET). Used both by `run` and by the cost estimate."""
    seg = read_json(cfg.path("segments.json"))
    if not seg:
        raise PipelineError("data.missing", "Run this first: python run.py extract", step="extract")
    queries = build_queries(cfg, seg["sentences"])
    if limit:
        queries = queries[:limit]
    names = backend_names(cfg)
    cache = KVCache(cfg.path("cache_search.sqlite"))
    # results from any previously used engine are acceptable (e.g. started with serper, continued with ddg)
    all_names = list(dict.fromkeys(names + [n for n in BACKENDS if n != "none"]))
    todo = [q for q in queries if cached_results(cache, cfg, all_names, q["q"]) is None]
    # first window of each sentence first, then the rest (with a limited budget the whole text still gets covered)
    todo.sort(key=lambda q: (q["win"], q["mode"] != "loose", q["sid"]))
    n_new = len(todo)
    if cfg.search_budget and len(todo) > cfg.search_budget:
        todo = todo[:cfg.search_budget]
    return dict(seg=seg, queries=queries, todo=todo, n_new=n_new, names=names, all_names=all_names, cache=cache)


def run(cfg: Config, limit: int | None = None, dry: bool = False, yes: bool = False, confirm=None):
    """`confirm(n_queries, engines) -> bool` is asked before paid queries are made (unless `yes`)."""
    pq = plan_queries(cfg, limit)
    seg, queries, todo, names, all_names, cache = (pq["seg"], pq["queries"], pq["todo"], pq["names"],
                                                    pq["all_names"], pq["cache"])
    write_json(cfg.path("queries.json"), queries)
    if len(todo) < pq["n_new"]:
        log(f"SEARCH_BUDGET={cfg.search_budget}: running {len(todo)} of {pq['n_new']} queries this time "
            f"(the rest can be run later – completed ones are not repeated)")
        warn("search.budget_limited", f"Search budget limits this run to {len(todo)} of {pq['n_new']} queries",
             run=len(todo), total=pq["n_new"])
    backends = get_backends(cfg) if names[0] != "none" else []
    if names[0] != "none":
        names = [b.name for b in backends]
    log(f"Queries: {len(queries)}, new (not cached): {len(todo)}; search: {' → '.join(names)}"
        + (f" (Serper: fits within the free 2,500 if the account is new)" if names[0] == "serper" and len(todo) <= 2500 else ""))
    if dry:
        return None
    if todo and names[0] != "none" and not yes:
        if confirm is None:
            def confirm(n, engines):
                ans = input(f"Run {n} search queries? [t/N]: ").strip().lower()
                return ans in ("t", "taip", "y", "yes")
        if not confirm(len(todo), names):
            raise PipelineError("aborted", "Aborted.")

    state = {"i": 0, "errors": 0, "stop": False}
    lock = threading.Lock()
    prog = Progress(len(todo), "Search", phase="search.queries")
    used: dict[str, int] = {}
    prog.set(counters={"cached": len(queries) - pq["n_new"], "queries": 0, "results": 0, "errors": 0},
             cost={"search": {}, "budget": cfg.search_budget or None, "engine": names[0] if names else None})

    def do(q):
        checkpoint()
        while True:
            with lock:
                if state["stop"] or state["i"] >= len(backends):
                    return None
                i = state["i"]
            b = backends[i]
            if b.name == "none":
                return None
            prog.set(current=activity("search", engine=b.name, query=q["plain"][:90]))
            try:
                res = b.search(q["q"])
            except CreditsExhausted as e:
                with lock:
                    if state["i"] == i:
                        state["i"] += 1
                        nxt = backends[state["i"]].name if state["i"] < len(backends) else None
                        log(f"{b.name}: credits/limit exhausted ({str(e)[:120]}). "
                            + (f"Switching to: {nxt}" if nxt else "No more engines – stopping."))
                        if nxt:
                            warn("search.engine_switched", f"{b.name} credits exhausted, switching to {nxt}",
                                 old=b.name, new=nxt)
                            prog.set(cost={"engine": nxt})
                        else:
                            warn("search.engines_exhausted", f"{b.name} credits exhausted, no more search engines",
                                 old=b.name)
                continue
            cache.set(_key(b.name, cfg, q["q"]), res)
            with lock:
                used[b.name] = used.get(b.name, 0) + 1
            prog.count("results", len(res))
            return res

    if todo and names[0] != "none":
        with ThreadPoolExecutor(max_workers=cfg.search_workers) as ex:
            futs = [ex.submit(do, q) for q in todo]
            for f in as_completed(futs):
                ok = True
                try:
                    f.result()
                except SearchError as e:
                    ok = False
                    with lock:
                        state["errors"] += 1
                    log("ERROR:", str(e)[:200])
                except Exception as e:
                    ok = False
                    with lock:
                        state["errors"] += 1
                    log("ERROR:", repr(e)[:200])
                prog.set(cost={"search": dict(used)})
                prog.tick(count="queries" if ok else "errors")
                if state["errors"] >= 15 and state["errors"] > 0.3 * prog.done:
                    state["stop"] = True
                    log("Too many search errors – stopping. Check the API key / internet connection.")
                    warn("search.too_many_errors", "Too many search errors – stopping. "
                                                   "Check the API key / internet connection.")
                    for x in futs:
                        x.cancel()
                    break
    left = sum(1 for q in queries if cached_results(cache, cfg, all_names, q["q"]) is None) \
        if names[0] != "none" else 0
    if left:
        log(f"Queries not completed: {left}. Running 'search' again will resume from here.")
        warn("search.incomplete", f"{left} queries not completed; running again resumes from here", left=left)
    cands = aggregate(cfg, seg, queries, cache, all_names)
    return dict(queries=len(queries), ran=sum(used.values()), used=used, left=left, candidates=len(cands),
                selected=sum(c["selected"] for c in cands), errors=state["errors"])


def aggregate(cfg: Config, seg: dict, queries: list[dict], cache: KVCache, names: list[str]):
    excl = DEFAULT_EXCLUDE | {d.strip().lower() for d in cfg.exclude_domains.split(",") if d.strip()}
    by_url: dict[str, dict] = {}
    best_hit: dict[tuple, dict] = {}
    for q in queries:
        res = cached_results(cache, cfg, names, q["q"]) or []
        for r in res:
            url = r.get("url", "")
            if not url.startswith("http"):
                continue
            dom = domain(url)
            if any(dom == d or dom.endswith("." + d) for d in excl):
                continue
            nu = norm_url(url)
            c = by_url.setdefault(nu, dict(url=url, nurl=nu, domain=dom, title=clean_text(r.get("title", "")),
                                           dates=set(), hits={}))
            if r.get("date"):
                c["dates"].add(r["date"])
            snip, sdate = split_snippet_meta(clean_text(r.get("snippet", "")))
            if sdate:
                c["dates"].add(sdate)
            m = snippet_match(q["plain"], snip)
            k = (nu, q["sid"])
            h = dict(sid=q["sid"], qid=q["qid"], rank=r.get("rank", 0), snippet=snip, **m)
            if k not in best_hit or (h["cov"], h["lcs"]) > (best_hit[k]["cov"], best_hit[k]["lcs"]):
                best_hit[k] = h
    for (nu, sid), h in best_hit.items():
        by_url[nu]["hits"][sid] = h

    # seeds (URL list that must always be checked)
    if cfg.seeds_file:
        try:
            for line in open(cfg.seeds_file, encoding="utf-8"):
                u = line.strip()
                if u.startswith("http"):
                    nu = norm_url(u)
                    by_url.setdefault(nu, dict(url=u, nurl=nu, domain=domain(u), title="", dates=set(), hits={}))
                    by_url[nu]["seed"] = True
        except FileNotFoundError:
            log(f"Seeds file not found: {cfg.seeds_file}")

    # bibliography sources found via OpenAlex (python run.py refs)
    for r in read_json(cfg.path("ref_seeds.json"), []) or []:
        nu = norm_url(r["url"])
        c = by_url.setdefault(nu, dict(url=r["url"], nurl=nu, domain=domain(r["url"]), title=r.get("title", ""),
                                       dates=set(), hits={}))
        c["seed"] = True
        c["ref_raw"] = r.get("ref_raw")
        if r.get("year"):
            c["dates"].add(str(r["year"]))

    cands = []
    for c in by_url.values():
        hits = list(c["hits"].values())
        good = [h for h in hits if h["lcs"] >= 5 and h["cov"] >= 0.35]
        strong = [h for h in hits if h["lcs"] >= 8 or h["cov"] >= 0.6]
        prio = sum(h["cov"] ** 2 * (1.0 if h["lcs"] >= 6 else 0.3) for h in hits)
        c.update(dates=sorted(c["dates"]), hits=sorted(hits, key=lambda h: h["sid"]),
                 n_hits=len(hits), n_good=len(good), n_strong=len(strong), priority=round(prio, 3),
                 selected=bool(c.get("seed")) or len(strong) >= 1 or len(good) >= 2 or
                 any(h["lcs"] >= 6 and h["cov"] >= 0.45 for h in hits))
        cands.append(c)
    cands.sort(key=lambda c: (-int(bool(c.get("seed"))), -c["priority"]))
    sel = [c for c in cands if c["selected"]]
    for c in sel[cfg.max_sources:]:
        c["selected"] = False
    write_json(cfg.path("candidates.json"), cands)
    n_sel = sum(c["selected"] for c in cands)
    covered = len({h["sid"] for c in cands if c["selected"] for h in c["hits"]})
    log(f"Candidate URLs: {len(cands)}, selected for download: {n_sel}; they cover {covered} sentences "
        f"-> {cfg.path('candidates.json')}")
    return cands
