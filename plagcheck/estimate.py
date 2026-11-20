"""Cost and time estimates (shown before a run) and LLM price lookup (used for live cost tracking).

All figures are approximate: the number of sources that end up being assessed is only known after the search.
The same formulas are used by the calculator on the project website (site/app.js).
"""
from __future__ import annotations

from .config import Config

# (substring of the model name, USD per 1M input tokens, USD per 1M output tokens); first match wins
LLM_PRICES = [
    ("opus", 5.0, 25.0), ("sonnet", 3.0, 15.0), ("haiku", 1.0, 5.0),
    ("gpt-4o-mini", 0.15, 0.6), ("gpt-4o", 2.5, 10.0), ("gpt-4.1-mini", 0.4, 1.6), ("gpt-4.1", 2.0, 8.0),
    ("gpt-5-mini", 0.25, 2.0), ("gpt-5", 1.25, 10.0),
]
DEFAULT_PRICE = (3.0, 15.0)
SEARCH_FREE = {"serper": 2500, "tavily": 1000}      # serper: one-time on sign-up; tavily: per month

# per-source token budget of the assessment prompt and the final conclusion
TOKENS_PER_SOURCE = (4300, 1400)
TOKENS_FINAL = (6000, 2500)
SOURCES_PER_WORD = 1 / 1800
SELECTED_PER_QUERY = 0.4


def llm_price(model: str | None) -> tuple[float, float]:
    m = (model or "").lower()
    for sub, i, o in LLM_PRICES:
        if sub in m:
            return i, o
    return DEFAULT_PRICE


def llm_cost(model: str | None, usage: dict | None) -> float:
    if not usage:
        return 0.0
    i, o = llm_price(model)
    return round(usage.get("input", 0) / 1e6 * i + usage.get("output", 0) / 1e6 * o, 4)


def estimate(cfg: Config, include_refs: bool = False) -> dict:
    from .collect import plan_queries
    from .search import BACKENDS, backend_names, _KEYS
    pq = plan_queries(cfg)
    seg = pq["seg"]
    words = seg["meta"].get("words", 0)
    chain = [n for n in backend_names(cfg) if n == "none" or n not in _KEYS or getattr(cfg, _KEYS[n], "")]
    engine = chain[0] if chain else "none"
    n_run = len(pq["todo"]) if engine != "none" else 0
    per1000 = getattr(BACKENDS.get(engine), "cost_per_1000", 0.0) or 0.0
    free = SEARCH_FREE.get(engine)
    search_usd = round(max(n_run - (free or 0), 0) / 1000 * per1000, 2)

    n_sources = max(5, min(80, round(words * SOURCES_PER_WORD)))
    use_llm = cfg.llm_provider != "none"
    llm = None
    if use_llm:
        i_tok = n_sources * TOKENS_PER_SOURCE[0] + TOKENS_FINAL[0]
        o_tok = n_sources * TOKENS_PER_SOURCE[1] + TOKENS_FINAL[1]
        usd = llm_cost(cfg.llm_model, {"input": i_tok, "output": o_tok})
        llm = dict(provider=cfg.llm_provider, model=cfg.llm_model, sources=n_sources, input_tokens=i_tok,
                   output_tokens=o_tok, usd=usd, usd_low=round(usd * 0.5, 2), usd_high=round(usd * 1.6, 2))

    # rough durations (seconds)
    q_rate = 1.0 if engine == "ddg" else min(cfg.search_workers, 6) * 1.3
    n_fetch = min(cfg.max_sources, round(len(pq["queries"]) * SELECTED_PER_QUERY))
    t_search = n_run / q_rate if n_run else 0
    t_fetch = n_fetch / 0.8
    t_verify = (n_sources * 25 / max(cfg.llm_workers, 1) + 60) if use_llm else 5
    t_refs = len(seg.get("references", [])) * 0.8 if include_refs else 0
    return dict(
        pages=seg["meta"].get("pages"), body_pages=seg["meta"].get("body_pages"), words=words,
        sentences=sum(1 for s in seg["sentences"] if s["align"]), references=len(seg.get("references", [])),
        queries=len(pq["queries"]), cached=len(pq["queries"]) - pq["n_new"], new=pq["n_new"], run_now=n_run,
        budget=cfg.search_budget or None, engines=chain, engine=engine, free_credits=free,
        search_usd=search_usd, search_per1000=per1000, llm=llm,
        total_usd_low=round(search_usd + (llm["usd_low"] if llm else 0), 2),
        total_usd_high=round(search_usd + (llm["usd_high"] if llm else 0), 2),
        minutes=round((t_search + t_fetch + t_verify + t_refs + 30) / 60),
    )
