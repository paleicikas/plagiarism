# Dissertation plagiarism check

A pipeline that checks whether a dissertation (`original.pdf`) reuses text from external sources — copied verbatim,
rewritten with a few words changed, or closely paraphrased — and whether this was done with proper attribution.
The final output is a self-contained `index.html` report (in Lithuanian).

```
PDF ─► 0. extract ─► 1. search ─► fetch ─► align ─► 2. verify (LLM) ─► 3. report (index.html)
       sentences     web search    source    full-text      labels and         visualisation
                                   texts     comparison     conclusions
```

## Installation

1. Python 3.10+.
2. In this folder:
   ```
   pip install -r requirements.txt
   cp .env.example .env        # Windows: copy .env.example .env
   ```
3. Fill in the keys in `.env` (never commit this file):
   - **SERPER_API_KEY** – https://serper.dev. New accounts get **2,500 free queries** (no card); this dissertation needs
     ~2,070. `SEARCH_BUDGET=2400` prevents exceeding the free limit.
   - **TAVILY_API_KEY** (optional) – https://tavily.com, 1,000 free queries per month. Used automatically if Serper
     credits run out.
   - Without any key, `ddg` (free, no key) is the last engine in the chain `SEARCH_BACKEND=serper,tavily,ddg`.
   - **ANTHROPIC_API_KEY** – https://console.anthropic.com (stage 2).
   - **UNPAYWALL_EMAIL** – any e-mail address (free; helps find open-access PDFs).

If the search is interrupted (credits exhausted, network failure), run `python run.py search -y` again — completed
queries are cached and not repeated; it resumes where it stopped (optionally with another engine).

## Running

Stage by stage (recommended — results can be inspected after each stage):

```
python run.py extract                 # 0: PDF -> ~1,900 sentences (work/segments.json)
python run.py search --dry-run        # number of queries and approximate cost
python run.py search --limit 50 -y    # trial run with 50 queries
python run.py search -y               # 1: full search (~2,070 queries – fits in Serper's free 2,500)
python run.py fetch                   # 1: download sources (work/sources.json, work/sources/*.txt)
python run.py align                   # 1: compare sources with the full text (work/matches.json)
python run.py verify                  # 2: LLM assessment and conclusion (work/verdicts.json)
                                      #    (model: LLM_MODEL, final conclusion: LLM_FINAL_MODEL)
python run.py report                  # 3: work/index.html
```

Or everything at once: `python run.py all -y`.

Additionally — check every entry of the reference list (OpenAlex / Crossref / Unpaywall; uses no Serper credits):
```
python run.py refs                    # reference-list sources + fetch + align
python run.py verify
python run.py report
```

Manually verified source metadata (authors, dates) goes into `source_overrides.json`.

All intermediate results are cached (`work/cache_*.sqlite`), so an interrupted run can be resumed — search and LLM
requests that were already made are not paid for again.

Without an LLM (automatic metrics only): `python run.py verify --llm none`.

## Improvements over a simpler sentence-search approach

| Problem | How it is handled here |
|---|---|
| Table of contents, tables and the reference list get checked | Only the main text is used; tables, headings and the TOC are dropped |
| Sources published **after** the dissertation are treated as possible plagiarism sources | Each source is dated (Crossref, OpenAlex, page metadata, URL, Google, Wayback archive); later ones are marked as "reverse" |
| Many sources are "unreachable" (ResearchGate, ScienceDirect, Emerald…) | Fallbacks: Europe PMC, OpenAlex/Crossref + Unpaywall, Wayback; if all fail, search snippets or abstracts are assessed (with lower confidence) |
| Only the sentence found by the search is assessed | Every source found is compared with the **entire** dissertation — whole copied passages and paraphrases are found |
| Quotations, standard questionnaire items and common phrases are not distinguished | The LLM assigns one of 9 labels; the tool checks whether the source is in the reference list and whether its authors are cited nearby |
| No estimate of extent | Words, % of text, pages and longest continuous passages are computed per source and overall |
| Copies of the dissertation itself and the author's own articles inflate the result | They are detected and reported separately, not counted |

## Additional sources

- `seeds_from_example.txt` – URLs found by an earlier tool; they are always downloaded and compared.
- `local_sources/` – put PDF/TXT/HTML files of suspected sources here (e.g. paywalled articles from the reference list).
  Put the year in the file name, e.g. `Gill_2002_review.pdf`, so the date can be checked too.

## Outputs (`work/`)

| File | Contents |
|---|---|
| `segments.json` | Sentences with page numbers, citation/quotation flags, parsed reference list |
| `queries.json`, `candidates.json` | Search queries and all URLs found, with snippet match metrics |
| `ref_seeds.json` | Reference-list entries found in OpenAlex/Crossref |
| `sources.json`, `sources/*.txt` | Downloaded sources, their dates and how they were obtained |
| `matches.json` | Sentence–source pairs, categories, continuous passages |
| `verdicts.json` | LLM labels, source verdicts, overall conclusion |
| `index.html` | The report (open in a browser, works offline) |

## Categories

Automatic (`align`): **identical** (≥90% of the dissertation sentence's words are in shared word sequences),
**minor edits** (60–90%), **partial** (35–60% and a ≥6-word shared sequence), **possible paraphrase**
(≥60% shared content words, weighting rarer words more).

LLM labels (`verify`): verbatim without reference · verbatim with the author cited but without quotation marks ·
close paraphrase without reference · paraphrase with reference · proper quotation · common phrase · reverse
(source is later) · author's own work · coincidental match. The first three are counted as problematic.

Thresholds can be tuned in `plagcheck/align.py` (`categorize`) and `plagcheck/collect.py` (which candidates are downloaded).

## Limitations

- Web search does not find unindexed / paywalled / print-only sources → use `local_sources/`.
- Free paraphrasing and translation from another language are only partly detected.
- The conclusion is an aid — the final decision must be made by a person who has reviewed the pairs in the report.
