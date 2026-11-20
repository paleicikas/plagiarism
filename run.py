#!/usr/bin/env python3
"""Dissertation plagiarism check (command line). The desktop app (app/main.py) runs the same pipeline.

Stages:
  extract  – PDF -> sentences                     (work/segments.json)
  search   – web search, candidates               (work/candidates.json)       [stage 1]
  fetch    – download sources, dates              (work/sources.json)          [stage 1]
  align    – compare all sources with the text    (work/matches.json)          [stage 1]
  verify   – LLM verification and conclusions     (work/verdicts.json)         [stage 2]
  report   – index.html visualization             (work/index.html)            [stage 3]
  all      – all of the above in order
  refs     – additionally: all bibliography sources (OpenAlex) + fetch + align
  estimate – approximate number of queries, cost and duration

Examples:
  python run.py extract
  python run.py search --dry-run          # how many queries and how much it will cost
  python run.py search --limit 50 --yes   # trial run with 50 queries
  python run.py all --yes
"""
import argparse
import json
import sys

from plagcheck.config import Config
from plagcheck.errors import Cancelled, PipelineError


def main():
    for st in (sys.stdout, sys.stderr):      # Windows console: Lithuanian characters
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["extract", "search", "fetch", "align", "verify", "report", "all", "refs",
                                     "estimate"])
    ap.add_argument("--pdf", help="Dissertation PDF (default: original.pdf)")
    ap.add_argument("--work", help="Working directory (default: work)")
    ap.add_argument("--pages", help="Pages to check, e.g. 10-174")
    ap.add_argument("--backend", help="serper | serpapi | brave | google_cse | tavily | ddg | none (or a chain)")
    ap.add_argument("--limit", type=int, help="Only the first N queries (for testing)")
    ap.add_argument("--dry-run", action="store_true", help="Only count queries and cost")
    ap.add_argument("--yes", "-y", action="store_true", help="Do not ask for confirmation")
    ap.add_argument("--llm", help="anthropic | openai | none")
    ap.add_argument("--model", help="LLM model, e.g. claude-sonnet-5-5")
    ap.add_argument("--lang", help="Report / conclusion language, e.g. en, lt")
    ap.add_argument("--top", type=int, help="verify: evaluate only the N most significant sources")
    ap.add_argument("--out", help="report: where to write the HTML")
    a = ap.parse_args()

    cfg = Config()
    if a.pdf: cfg.pdf = a.pdf
    if a.work: cfg.work = a.work
    if a.pages: cfg.body_pages = a.pages
    if a.backend: cfg.search_backend = a.backend
    if a.llm: cfg.llm_provider = a.llm
    if a.model: cfg.llm_model = a.model
    if a.lang: cfg.report_lang = a.lang

    if a.step == "estimate" or (a.step == "search" and a.dry_run):
        from plagcheck.estimate import estimate
        print(json.dumps(estimate(cfg), ensure_ascii=False, indent=1))
        return

    from plagcheck.pipeline import run_pipeline
    if a.step == "all":
        steps = ["extract", "search", "fetch", "align", "verify", "report"]
    elif a.step == "refs":
        steps = ["refs", "reaggregate", "fetch", "align"]
    else:
        steps = [a.step]

    def on_event(e):
        if e["type"] == "step_start":
            print(f"\n=== {e['step'].upper()} ===", file=sys.stderr)
        elif e["type"] == "step_done":
            print(f"({e['step']}: {e['duration_s']} s)", file=sys.stderr)

    try:
        run_pipeline(cfg, steps, on_event=on_event, limit=a.limit, yes=a.yes, only_top=a.top, out=a.out,
                     report_lang=a.lang)
    except PipelineError as e:
        raise SystemExit(f"ERROR: {e}")
    except (Cancelled, KeyboardInterrupt):
        raise SystemExit("Interrupted.")


if __name__ == "__main__":
    main()
