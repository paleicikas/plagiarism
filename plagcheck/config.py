"""Settings. Values are read from the .env file / environment variables; CLI arguments override them."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # python-dotenv is optional
    pass


def _env(name, default=None, cast=str):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    if cast is bool:
        return v.strip().lower() in ("1", "true", "yes", "taip")
    return cast(v)


def parse_pages(spec: str) -> list[int]:
    """'10-174,191' -> [10..174, 191] (1-based)."""
    pages: list[int] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a), int(b) + 1))
        else:
            pages.append(int(part))
    return pages


@dataclass
class Config:
    pdf: str = _env("PDF", "original.pdf")
    work: str = _env("WORK_DIR", "work")
    # Dissertation metadata (used for date comparison and as LLM context)
    doc_title: str = _env("DOC_TITLE", "")
    doc_author: str = _env("DOC_AUTHOR", "")
    doc_date: str = _env("DOC_DATE", "")            # e.g. 2015-07-21; empty -> from PDF metadata
    author_markers: str = _env("AUTHOR_MARKERS", "")  # comma-separated: words indicating the author's own work
    body_pages: str = _env("BODY_PAGES", "")          # e.g. "10-174"; empty -> automatic
    ref_pages: str = _env("REF_PAGES", "")            # reference list pages; empty -> automatic

    # Search
    # serper | serpapi | brave | google_cse | tavily | ddg | none; chain: "serper,tavily,ddg"
    search_backend: str = _env("SEARCH_BACKEND", "serper")
    search_budget: int = _env("SEARCH_BUDGET", 0, int)       # max new queries per run (0 = unlimited)
    serper_key: str = _env("SERPER_API_KEY", "")
    serpapi_key: str = _env("SERPAPI_API_KEY", "")
    brave_key: str = _env("BRAVE_API_KEY", "")
    tavily_key: str = _env("TAVILY_API_KEY", "")
    google_key: str = _env("GOOGLE_API_KEY", "")
    google_cx: str = _env("GOOGLE_CSE_ID", "")
    search_gl: str = _env("SEARCH_GL", "us")
    search_hl: str = _env("SEARCH_HL", "en")
    search_num: int = _env("SEARCH_NUM", 10, int)
    search_workers: int = _env("SEARCH_WORKERS", 6, int)
    query_mode: str = _env("QUERY_MODE", "loose")     # loose | exact | both
    query_max_words: int = _env("QUERY_MAX_WORDS", 30, int)
    query_min_words: int = _env("QUERY_MIN_WORDS", 9, int)
    exclude_domains: str = _env("EXCLUDE_DOMAINS", "")   # additional domains to exclude

    # Source downloading
    fetch_workers: int = _env("FETCH_WORKERS", 8, int)
    fetch_timeout: int = _env("FETCH_TIMEOUT", 30, int)
    max_sources: int = _env("MAX_SOURCES", 900, int)
    unpaywall_email: str = _env("UNPAYWALL_EMAIL", "")
    use_wayback: bool = _env("USE_WAYBACK", True, bool)
    local_sources: str = _env("LOCAL_SOURCES", "local_sources")   # folder with PDF/TXT of suspected sources
    seeds_file: str = _env("SEEDS_FILE", "")                      # list of URLs to always check
    overrides_file: str = _env("OVERRIDES_FILE", "source_overrides.json")  # manually verified source data

    # LLM
    llm_provider: str = _env("LLM_PROVIDER", "anthropic")   # anthropic | openai | none
    anthropic_key: str = _env("ANTHROPIC_API_KEY", "")
    openai_key: str = _env("OPENAI_API_KEY", "")
    openai_base_url: str = _env("OPENAI_BASE_URL", "")
    llm_model: str = _env("LLM_MODEL", "claude-sonnet-5-5")
    llm_final_model: str = _env("LLM_FINAL_MODEL", "")       # empty -> same as llm_model
    llm_workers: int = _env("LLM_WORKERS", 4, int)
    llm_max_pairs: int = _env("LLM_MAX_PAIRS", 40, int)
    # Language of the report and of the LLM-written conclusions (a locale code from plagcheck/locales)
    report_lang: str = _env("REPORT_LANG", "lt")

    extra: dict = field(default_factory=dict)

    @property
    def work_dir(self) -> Path:
        p = Path(self.work)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def path(self, name: str) -> Path:
        return self.work_dir / name

    def to_dict(self):
        d = asdict(self)
        for k in list(d):
            if k.endswith("_key") and d[k]:
                d[k] = "***"
        return d

    @classmethod
    def blank(cls, **overrides) -> "Config":
        """A configuration that ignores .env / environment values (used by the desktop app, which keeps its own
        settings); `overrides` set the fields explicitly."""
        import dataclasses
        c = cls()
        for f in dataclasses.fields(cls):
            if f.name == "extra":
                continue
            # recompute the default as if no environment variable were set
            default = _BLANK_DEFAULTS.get(f.name, f.default)
            setattr(c, f.name, default)
        for k, v in overrides.items():
            if not hasattr(c, k):
                raise AttributeError(f"Unknown config field: {k}")
            setattr(c, k, v)
        return c


_BLANK_DEFAULTS = dict(
    pdf="original.pdf", work="work", doc_title="", doc_author="", doc_date="", author_markers="", body_pages="",
    ref_pages="", search_backend="ddg", search_budget=0, serper_key="", serpapi_key="", brave_key="",
    tavily_key="", google_key="", google_cx="", search_gl="us", search_hl="en", search_num=10, search_workers=6,
    query_mode="loose", query_max_words=30, query_min_words=9, exclude_domains="", fetch_workers=8,
    fetch_timeout=30, max_sources=900, unpaywall_email="", use_wayback=True, local_sources="local_sources",
    seeds_file="", overrides_file="", llm_provider="none", anthropic_key="", openai_key="", openai_base_url="",
    llm_model="claude-sonnet-5-5", llm_final_model="", llm_workers=4, llm_max_pairs=40, report_lang="en",
)
