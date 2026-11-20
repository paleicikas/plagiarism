# Plagiarism – thesis plagiarism checker

[![Build](https://github.com/paleicikas/plagiarism/actions/workflows/build.yml/badge.svg)](https://github.com/paleicikas/plagiarism/actions/workflows/build.yml)
[![Website](https://github.com/paleicikas/plagiarism/actions/workflows/pages.yml/badge.svg)](https://paleicikas.github.io/plagiarism/)
[![Latest release](https://img.shields.io/github/v/release/paleicikas/plagiarism?label=download)](https://github.com/paleicikas/plagiarism/releases/latest)

A desktop app (Windows, macOS, Linux) that checks whether a dissertation or thesis PDF reuses text from online
sources – copied verbatim, rewritten with a few words changed, or closely paraphrased – and whether the source was
properly cited. Add a PDF, press **Start**, and the app searches the web, downloads the sources, compares them with the
whole text, lets an AI model judge each match and shows an interactive report you can export to HTML, PDF, CSV, JSON
or ZIP.

**Website and guide:** <https://paleicikas.github.io/plagiarism/> · The interface is in English and Lithuanian
(switch in *Settings*); more languages can be added with a single file.

```
PDF ─► extract ─► search ─► fetch ─► align ─► verify (AI) ─► report
      sentences   web search  sources   full-text   labels and     interactive
                                        comparison  conclusions    HTML + exports
```

---

## For users

### 1. Download and install

Get the newest build from **[Releases](https://github.com/paleicikas/plagiarism/releases/latest)**:

| System | File | Notes |
|---|---|---|
| Windows 10/11 | `Plagiarism-…-windows-x64-setup.exe` | Installs for the current user, no admin rights needed. A portable `.zip` is also available. |
| macOS 11+ (Apple silicon) | `Plagiarism-…-macos-arm64.dmg` | Drag *Plagiarism* to *Applications*. |
| Linux (x86-64) | `Plagiarism-…-linux-x86_64.AppImage` | `chmod +x Plagiarism-*.AppImage` and run it. |

The builds are not code-signed, so the system warns the first time:

- **Windows SmartScreen** – "Windows protected your PC": click **More info → Run anyway**.
- **macOS Gatekeeper** – "cannot be opened because the developer cannot be verified": right-click the app in
  *Applications* → **Open** → **Open**. (Or *System Settings → Privacy & Security → Open Anyway*.)
- **Linux** – if the AppImage does not start, install FUSE 2 (`sudo apt install libfuse2`) or run it with
  `--appimage-extract-and-run`.

`SHA256SUMS.txt` in every release lets you verify the downloads.

### 2. First start – API keys

A short wizard asks for the interface language and the API keys. Keys are stored in the operating system's
credential store (Windows Credential Manager, macOS Keychain, Secret Service on Linux) – never in plain project files.
Each key has a **Test** button.

| Service | Used for | Cost |
|---|---|---|
| [Serper](https://serper.dev) | Google web search (recommended) | 2,500 free queries for new accounts, then ≈ $1 per 1,000 |
| [Tavily](https://tavily.com) | Web search, used automatically when Serper runs out | 1,000 free queries per month |
| [Anthropic](https://console.anthropic.com) or [OpenAI](https://platform.openai.com) | AI judgement of each match and the overall conclusion | About $0.50–1.50 for a 200-page thesis with Claude Sonnet |
| [Unpaywall](https://unpaywall.org/products/api) | Finding open-access PDFs of articles | Free – only your e-mail address |

No keys at all? The app still works: it can use free DuckDuckGo search (slower, fewer results; enable it in
*Settings*) and skip the AI step (automatic similarity metrics only). The built-in **demo** runs completely offline.

### 3. Check a document

1. **New check** → drop a PDF (or click to choose one).
2. Review the detected title, author and date, the pages to check and the options. The **estimate** shows the number
   of search queries, the expected cost and duration before anything is spent.
3. Optionally add **local sources** – PDF/TXT/HTML files of suspected sources that are not freely online (e.g.
   paywalled articles from the reference list). Put the year in the file name, e.g. `Smith_2002_review.pdf`.
4. Press **Start**. The progress screen shows every step, what is being done right now, counters, cost so far,
   warnings and a full log. You can pause, resume or cancel; an interrupted check continues where it stopped – search
   and AI requests already made are cached and not paid for again. The computer is kept awake and you get a system
   notification at the end.
5. The **report** opens in the app. Export it as a self-contained HTML (works offline, has its own language switch),
   a PDF summary, CSV/JSON data or a ZIP with everything.

All checks are listed in **History**. Data stays on your computer, in the app data folder (see *Settings → About*):
the only things sent out are search queries (short phrases from the text) to the search service and the matched
sentence pairs to the AI provider you configured.

### How to read the report

- **Identical** – at least 90 % of the sentence's words are in word sequences shared with the source;
  **minor edits** 60–90 %; **partial** 35–60 % with a shared sequence of ≥ 6 words; **possible paraphrase** – ≥ 60 %
  shared content words (rarer words weigh more).
- The AI assigns one of 9 labels: verbatim without reference · verbatim, author cited but no quotation marks · close
  paraphrase without reference · paraphrase with reference · proper quotation · common phrase · reverse (the source
  is later than the thesis) · author's own work · coincidental match. **The first three count as problematic.**
- Sources published after the thesis are marked as *reverse*; copies of the thesis itself and the author's own
  articles are reported separately and not counted.

### Limitations

- Web search does not find unindexed, paywalled or print-only sources – add them as local sources.
- Free paraphrasing and translation from another language are only partly detected.
- The result is an aid: the final decision must be made by a person who has reviewed the pairs in the report.

---

## For developers

### Run from source

Python 3.10+ (CI builds with 3.12).

```
pip install -r requirements.txt
python -m app.main            # the desktop app (add --debug for the WebView developer tools)
python -m pytest              # tests
```

On Linux the app window needs either GTK (`pip install "pywebview[gtk]"` plus the system WebKitGTK packages) or Qt
(`pip install "pywebview[qt]" PyQt6-WebEngine`).

UI development without a native window: `python -m app.devserver` serves the interface at
<http://127.0.0.1:8770/> with the same Python API behind an HTTP bridge. Set `PLAGCHECK_CONFIG_DIR` and
`PLAGCHECK_DATA_DIR` to temporary folders to keep test settings and keys away from your real ones.

```
app/            desktop app: main.py (window), api.py (methods called from JS), runner.py (background run,
                progress state), settings.py (settings + key store), projects.py, ui/ (HTML/CSS/JS)
plagcheck/      the engine: pipeline.py and one module per step, i18n.py + locales/, template.html (report),
                export.py, estimate.py, demo.py (offline synthetic demo)
packaging/      PyInstaller spec, Windows installer (Inno Setup), macOS dmg and Linux AppImage scripts, icons
site/           the GitHub Pages website
```

### Command line

The engine also runs without the app. Configuration comes from `.env` (copy `.env.example`; never commit it):

```
python run.py estimate                # number of queries, cost and duration
python run.py all -y --pdf thesis.pdf --work work --lang en
python run.py extract                 # or step by step: extract, search, fetch, align, verify, report
python run.py refs                    # also check every reference-list entry (OpenAlex / Crossref / Unpaywall)
python run.py verify --llm none       # automatic metrics only
python -m plagcheck.demo --out demo.html --lang en    # offline demo report
```

Outputs in the work folder: `segments.json` (sentences), `queries.json` / `candidates.json` (search), `sources.json`
+ `sources/*.txt` (downloaded sources and their dates), `matches.json` (sentence–source pairs), `verdicts.json`
(AI labels and conclusions), `index.html` (report). Everything is cached in `cache_*.sqlite`, so a run can be resumed.
Manually verified source metadata goes into `source_overrides.json`; URLs that should always be compared go into a
seeds file. Thresholds: `plagcheck/align.py` (`categorize`) and `plagcheck/collect.py`.

### Adding a language

1. Copy `plagcheck/locales/en.json` to `plagcheck/locales/<code>.json` (e.g. `de.json`) and translate the values.
   Keep the keys and `{placeholders}` unchanged; set `_meta.name` (native name), `_meta.english_name` and
   `_meta.locale`. The app, the report and the exports pick the file up automatically, and the AI writes its
   conclusions in that language when it is chosen as the report language.
2. For the website, do the same with `site/locales/en.json` and add the code to `LANGS` in `site/app.js`.
3. Run `python -m pytest tests/test_locales.py` – it fails if any key is missing or a placeholder differs.

### Builds and releases

`.github/workflows/build.yml` runs on every push to `main`: tests, then PyInstaller builds on Windows, macOS
(Apple silicon) and Linux, a `--selftest` of every build, packaging (Inno Setup installer + portable zip,
dmg, AppImage) and a GitHub release `v<X.Y>.<run number>` (X.Y from `pyproject.toml`) marked as latest. Only the 10
newest releases are kept. Pull requests build the same files as workflow artifacts. Changes only to `site/` or
Markdown files do not trigger a build; they deploy the website through `.github/workflows/pages.yml`.

Local build:

```
pip install -r requirements-dev.txt
pyinstaller packaging/plagcheck.spec --noconfirm
dist/Plagiarism/Plagiarism --selftest            # macOS: dist/Plagiarism.app/Contents/MacOS/Plagiarism --selftest
```

`python packaging/make_icons.py` regenerates all icons. The website is static: open `site/index.html` through any
local web server (`python -m http.server -d site`).
