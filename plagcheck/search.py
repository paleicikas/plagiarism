"""Search API adapters. All return [{url, title, snippet, date, rank}]."""
from __future__ import annotations

import time

import requests

from .config import Config


class SearchError(Exception):
    pass


class CreditsExhausted(SearchError):
    """API credits / monthly limit exhausted – fall back to the next search engine."""
    pass


_EXHAUST_WORDS = ("credit", "quota", "limit", "usage", "plan", "exceed", "balance", "insufficient", "payment")


def _retry(fn, tries=4):
    delay = 2.0
    for i in range(tries):
        try:
            return fn()
        except SearchError as e:
            if isinstance(e, CreditsExhausted) or ("HTTP 4" in str(e) and "429" not in str(e)):
                raise
            if i == tries - 1:
                raise
        except requests.RequestException:
            if i == tries - 1:
                raise
        time.sleep(delay)
        delay *= 2


class Backend:
    name = "base"
    cost_per_1000 = 0.0

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.s = requests.Session()

    def search(self, q: str) -> list[dict]:
        raise NotImplementedError

    def _check(self, r):
        if r.status_code != 200:
            msg = f"{self.name}: HTTP {r.status_code}: {r.text[:300]}"
            low = r.text[:1000].lower()
            if r.status_code in (402, 432, 433) or (r.status_code in (400, 401, 403) and
                                                     any(w in low for w in _EXHAUST_WORDS)):
                raise CreditsExhausted(msg)
            raise SearchError(msg)
        return r.json()


class Serper(Backend):
    """https://serper.dev — Google results, ~0.3–1 USD / 1000 queries."""
    name, cost_per_1000 = "serper", 1.0

    def search(self, q):
        if not self.cfg.serper_key:
            raise SearchError("SERPER_API_KEY is not set")

        def go():
            r = self.s.post("https://google.serper.dev/search", timeout=30,
                            headers={"X-API-KEY": self.cfg.serper_key, "Content-Type": "application/json"},
                            json={"q": q, "num": self.cfg.search_num, "gl": self.cfg.search_gl,
                                  "hl": self.cfg.search_hl})
            return self._check(r)
        data = _retry(go)
        return [dict(url=o.get("link", ""), title=o.get("title", ""), snippet=o.get("snippet", ""),
                     date=o.get("date", ""), rank=o.get("position", i + 1))
                for i, o in enumerate(data.get("organic", [])) if o.get("link")]


class SerpApi(Backend):
    """https://serpapi.com — Google results."""
    name, cost_per_1000 = "serpapi", 10.0

    def search(self, q):
        if not self.cfg.serpapi_key:
            raise SearchError("SERPAPI_API_KEY is not set")

        def go():
            r = self.s.get("https://serpapi.com/search.json", timeout=40,
                           params={"engine": "google", "q": q, "num": self.cfg.search_num,
                                   "gl": self.cfg.search_gl, "hl": self.cfg.search_hl,
                                   "api_key": self.cfg.serpapi_key})
            return self._check(r)
        data = _retry(go)
        return [dict(url=o.get("link", ""), title=o.get("title", ""), snippet=o.get("snippet", ""),
                     date=o.get("date", ""), rank=o.get("position", i + 1))
                for i, o in enumerate(data.get("organic_results", [])) if o.get("link")]


class Brave(Backend):
    """https://brave.com/search/api — independent index (not Google)."""
    name, cost_per_1000 = "brave", 5.0

    def search(self, q):
        if not self.cfg.brave_key:
            raise SearchError("BRAVE_API_KEY is not set")

        def go():
            r = self.s.get("https://api.search.brave.com/res/v1/web/search", timeout=30,
                           headers={"X-Subscription-Token": self.cfg.brave_key, "Accept": "application/json"},
                           params={"q": q, "count": min(self.cfg.search_num, 20)})
            return self._check(r)
        data = _retry(go)
        res = (data.get("web") or {}).get("results", [])
        out = []
        for i, o in enumerate(res):
            snip = o.get("description", "")
            for extra in o.get("extra_snippets", []) or []:
                snip += " ... " + extra
            out.append(dict(url=o.get("url", ""), title=o.get("title", ""), snippet=snip,
                            date=o.get("page_age", "") or o.get("age", ""), rank=i + 1))
        return out


class GoogleCSE(Backend):
    """Google Programmable Search JSON API (100 free/day, then 5 USD/1000)."""
    name, cost_per_1000 = "google_cse", 5.0

    def search(self, q):
        if not (self.cfg.google_key and self.cfg.google_cx):
            raise SearchError("GOOGLE_API_KEY and GOOGLE_CSE_ID are not set")

        def go():
            r = self.s.get("https://www.googleapis.com/customsearch/v1", timeout=30,
                           params={"key": self.cfg.google_key, "cx": self.cfg.google_cx, "q": q,
                                   "num": min(self.cfg.search_num, 10)})
            return self._check(r)
        data = _retry(go)
        return [dict(url=o.get("link", ""), title=o.get("title", ""), snippet=o.get("snippet", ""),
                     date="", rank=i + 1) for i, o in enumerate(data.get("items", []) or [])]


class Tavily(Backend):
    """https://tavily.com — 1,000 free queries per month (no card required)."""
    name, cost_per_1000 = "tavily", 8.0

    def search(self, q):
        if not self.cfg.tavily_key:
            raise SearchError("TAVILY_API_KEY is not set")

        def go():
            r = self.s.post("https://api.tavily.com/search", timeout=40,
                            headers={"Authorization": f"Bearer {self.cfg.tavily_key}"},
                            json={"query": q[:390], "max_results": min(self.cfg.search_num, 20),
                                  "search_depth": "basic"})
            return self._check(r)
        data = _retry(go)
        return [dict(url=o.get("url", ""), title=o.get("title", ""), snippet=o.get("content", ""),
                     date=o.get("published_date", "") or "", rank=i + 1)
                for i, o in enumerate(data.get("results", []) or []) if o.get("url")]


class DDG(Backend):
    """Free search without a key (`ddgs` package: DuckDuckGo, Bing, Brave, Mojeek, etc.).
    Slower and less accurate than Google, may be rate-limited."""
    name, cost_per_1000 = "ddg", 0.0

    def __init__(self, cfg):
        super().__init__(cfg)
        try:
            from ddgs import DDGS
        except ImportError:
            raise SystemExit("Install: pip install ddgs")
        self.DDGS = DDGS
        self.lock = __import__("threading").Lock()
        self.last = 0.0

    def search(self, q):
        delay = 1.5
        for attempt in range(5):
            with self.lock:            # no more often than ~1 query per second
                wait = self.last + 1.0 - time.time()
                if wait > 0:
                    time.sleep(wait)
                self.last = time.time()
            try:
                res = self.DDGS(timeout=20).text(q, max_results=self.cfg.search_num, backend="auto") or []
                return [dict(url=o.get("href", ""), title=o.get("title", ""), snippet=o.get("body", ""),
                             date="", rank=i + 1) for i, o in enumerate(res) if o.get("href")]
            except Exception as e:
                if "No results" in str(e):
                    return []
                if attempt == 4:
                    raise SearchError(f"ddg: {e}")
                time.sleep(delay)
                delay *= 2
        return []


class NoSearch(Backend):
    name = "none"

    def search(self, q):
        return []


BACKENDS = {"serper": Serper, "serpapi": SerpApi, "brave": Brave, "google_cse": GoogleCSE, "tavily": Tavily,
            "ddg": DDG, "none": NoSearch}


def backend_names(cfg: Config) -> list[str]:
    names = [n.strip().lower() for n in cfg.search_backend.split(",") if n.strip()]
    for n in names:
        if n not in BACKENDS:
            raise SystemExit(f"Unknown SEARCH_BACKEND '{n}'. Available: {', '.join(BACKENDS)}")
    return names or ["none"]


_KEYS = {"serper": "serper_key", "serpapi": "serpapi_key", "brave": "brave_key", "google_cse": "google_key",
         "tavily": "tavily_key"}


def get_backends(cfg: Config) -> list[Backend]:
    """SEARCH_BACKEND can be a chain, e.g. "serper,tavily,ddg": when credits run out, the next one is used.
    Engines without a configured API key are skipped."""
    out = []
    for n in backend_names(cfg):
        if n in _KEYS and not getattr(cfg, _KEYS[n], ""):
            continue
        out.append(BACKENDS[n](cfg))
    if not out:
        raise SystemExit("No API key is configured for any search engine (.env). "
                         "Set SERPER_API_KEY or use SEARCH_BACKEND=ddg.")
    return out


def get_backend(cfg: Config) -> Backend:
    return get_backends(cfg)[0]
