"""Stage 3: self-contained index.html report (no external dependencies, works offline).

All interface texts come from the locale files; every available language is embedded, so the language can be
switched inside the report. Texts written by the LLM stay in the language they were generated in."""
from __future__ import annotations

import html
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from . import i18n
from .config import Config
from .errors import PipelineError
from .util import log, read_json, strip_accents
from .verify import PROBLEM_LABELS

# label -> display class
CLASS_OF = {
    "pazodinis_be_nuorodos": "copy",
    "perfrazuota_be_nuorodos": "para",
    "pazodinis_su_nuoroda_be_kabuciu": "noquote",
    "perfrazuota_su_nuoroda": "ok",
    "tinkamas_citavimas": "ok",
    "autoriaus_darbas": "na",
    "atvirkstinis": "na",
    "bendrine_fraze": "na",
    "klaidingas_sutapimas": "na",
}
# display class -> colour (texts: class.<id> in the locale files)
CLASSES = [("copy", "#d03b3b"), ("para", "#ec835a"), ("noquote", "#fab219"), ("ok", "#2a78d6"), ("na", "#a3a29b")]
USAGE_ID = {"pažodinis kopijavimas": "verbatim", "kopijavimas su nedideliais pakeitimais": "minor_edits",
            "perfrazavimas": "paraphrase", "struktūros / argumentų sekos perėmimas": "structure",
            "citavimas": "quotation"}
RISK_ID = {"žema": "low", "vidutinė": "medium", "aukšta": "high"}
OLD_AUTO_NOTE = "(automatinė žyma)"
OLD_AUTO_SUMMARY = "Automatinis įvertinimas pagal sutapimų metrikas."
LOCALE_SECTIONS = ["report", "label", "verdict", "class", "usage", "risk", "status", "date", "cat", "reason", "auto"]


def _wtok(w: str) -> str:
    return re.sub(r"[^\w]", "", strip_accents(w).lower())


def diff_html(a: str, b: str) -> tuple[str, str]:
    """Highlights matching word sequences (≥2 words) in both texts."""
    aw, bw = a.split(), b.split()
    at, bt = [_wtok(w) for w in aw], [_wtok(w) for w in bw]
    sm = SequenceMatcher(None, at, bt, autojunk=False)
    am, bm = [False] * len(aw), [False] * len(bw)
    for blk in sm.get_matching_blocks():
        if blk.size >= 2:
            for k in range(blk.size):
                am[blk.a + k] = True
                bm[blk.b + k] = True

    def render(words, marks):
        out, run = [], []
        cur = None
        for w, m in zip(words, marks):
            if m != cur and run:
                t = html.escape(" ".join(run))
                out.append(f"<mark>{t}</mark>" if cur else t)
                run = []
            cur = m
            run.append(w)
        if run:
            t = html.escape(" ".join(run))
            out.append(f"<mark>{t}</mark>" if cur else t)
        return " ".join(out)
    return render(aw, am), render(bw, bm)


def trim_source(d_text: str, s_text: str, pad: int = 12) -> str:
    """Trims a long source window down to the matching part plus a few words of context."""
    sw = s_text.split()
    if len(sw) <= 70:
        return s_text
    at, bt = [_wtok(w) for w in d_text.split()], [_wtok(w) for w in sw]
    sm = SequenceMatcher(None, at, bt, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 2]
    if not blocks:
        return " ".join(sw[:70]) + " …"
    lo, hi = max(0, blocks[0].b - pad), min(len(sw), blocks[-1].b + blocks[-1].size + pad)
    return ("… " if lo > 0 else "") + " ".join(sw[lo:hi]) + (" …" if hi < len(sw) else "")


JUNK_TITLE = re.compile(r"(\.book\b|\.fm\)|\.docx?\b|\.indd\b|^microsoft word|^untitled|^bookfile|^\(pdf\)\s*$)", re.I)


def display_title(s: dict) -> tuple[str, dict | None]:
    """(title, i18n replacement) – PDF metadata "titles" (e.g. "Microsoft Word - ...doc") are replaced with the
    real title, or with a translated description when none is known."""
    t = (s.get("title") or "").strip()
    if s.get("kind") == "copy":
        return "Copy of the document online", dict(k="report.title_copy", p={})
    if t and not JUNK_TITLE.search(t) and t != s.get("domain"):
        return t, None
    for alt in ((s.get("openalex") or {}).get("title"), (s.get("crossref") or {}).get("title")):
        if alt:
            return alt, None
    if s.get("ref_match"):
        return s["ref_match"][0][:160], None
    if t and JUNK_TITLE.search(t):
        dom = s.get("domain", "")
        return f"Document on {dom}", dict(k="report.title_on_site", p=dict(domain=dom))
    return t or s.get("domain", ""), None


def build_data(cfg: Config, lang: str | None = None) -> dict:
    seg = read_json(cfg.path("segments.json"))
    mt = read_json(cfg.path("matches.json"))
    vd = read_json(cfg.path("verdicts.json"))
    if not (seg and mt and vd):
        raise PipelineError("data.missing", "Missing data: run extract, search, fetch, align, verify.", step="verify")
    sents = seg["sentences"]
    stats = vd["stats"]
    slab = {int(k): v for k, v in stats["sentence_labels"].items()}
    ssrc = {int(k): v for k, v in stats["sentence_source"].items()}
    verdicts = vd["verdicts"]

    sources = []
    for s in mt["sources"]:
        if not s["relevant"] or s["sid"] not in verdicts:
            continue
        v = verdicts[s["sid"]]
        lab = {int(p["id"]): p for p in v["pairs"]}
        pairs = []
        for p in s["pairs"]:
            d = sents[p["sid"]]["text"]
            st = trim_source(d, p["src_text"])
            dh, sh = diff_html(d, st)
            L = lab.get(p["sid"], {})
            auto = bool(L.get("auto")) or L.get("note") == OLD_AUTO_NOTE
            pairs.append(dict(sid=p["sid"], page=p["page"], cat=p["cat"], cov=p["cov"], cont=p["cont"],
                              label=L.get("label", ""), note="" if auto else L.get("note", ""), auto=auto,
                              d=dh, s=sh, ev=p["evidence"]))
        cls_words = defaultdict(int)
        for p in pairs:
            cls_words[CLASS_OF.get(p["label"], "na")] += sents[p["sid"]]["words"]
        prob_words = sum(sents[p["sid"]]["words"] for p in pairs if p["label"] in PROBLEM_LABELS)
        title, tk = display_title(s)
        summary = v.get("summary", "") or ""
        auto_summary = bool(v.get("auto")) or summary == OLD_AUTO_SUMMARY or (not summary and not v.get("llm"))
        sources.append(dict(
            id=s["sid"], url=s["url"], title=title, tk=tk, domain=s["domain"],
            status=s.get("status"), via=s.get("via"), evidence=s["evidence"], date=s.get("pub_date"),
            date_status=s.get("date_status"), date_note=s.get("date_note"), self=s.get("self"),
            in_refs=s.get("in_refs"), surnames=s.get("surnames"), mentions_author=s.get("mentions_author"),
            ref_match=s.get("ref_match") or [], n_pairs=s["n_pairs"], words=s["words"],
            pct=s["pct"], longest=s["longest"],
            pages=sorted({p["page"] for p in pairs}), verdict=v.get("verdict"), confidence=v.get("confidence"),
            usage=[USAGE_ID.get(u, u) for u in v.get("usage_types", []) or []],
            summary="" if auto_summary else summary, auto_summary=auto_summary,
            prob_words=prob_words, prob_pct=round(100 * prob_words / max(stats["total_words"], 1), 2),
            cls_words=cls_words, pairs=pairs, local=bool(s.get("local"))))
    sources.sort(key=lambda x: (-x["prob_words"], -x["words"]))

    others = []
    rel_ids = {s["id"] for s in sources}
    for s in mt["sources"]:
        if s["sid"] in rel_ids:
            continue
        if s.get("kind") == "copy":
            reason = "copy"
        elif s.get("kind") == "self":
            reason = "self"
        elif s.get("status") != "ok":
            reason = "not_fetched"
        else:
            reason = "no_matches"
        title, tk = display_title(s)
        others.append(dict(url=s["url"], title=title, tk=tk, domain=s["domain"], local=bool(s.get("local")),
                           status=s.get("status"), date=s.get("pub_date"), date_status=s.get("date_status"),
                           hits=s.get("n_search_hits", 0), pairs=s["n_pairs"], reason=reason,
                           kind=s.get("kind", "normal")))
    others.sort(key=lambda x: (x["kind"] == "normal", -x["pairs"], -x["hits"]))
    cands = read_json(cfg.path("candidates.json"), [])
    n_unselected = sum(1 for c in cands if not c.get("selected"))

    # pages
    pages = {}
    for s in sents:
        if not s["align"]:
            continue
        pg = pages.setdefault(s["page"], dict(p=s["page"], w=0, c=defaultdict(int)))
        pg["w"] += s["words"]
        if s["id"] in slab:
            pg["c"][CLASS_OF.get(slab[s["id"]], "na")] += s["words"]
    b0, b1 = seg["meta"]["body_pages"]
    page_list = [pages.get(p, dict(p=p, w=0, c={})) for p in range(b0, b1 + 1)]

    doc = [dict(i=s["id"], p=s["page"], t=s["text"], l=CLASS_OF.get(slab.get(s["id"]), "") if s["id"] in slab else "",
                lab=slab.get(s["id"], ""), src=ssrc.get(s["id"], "")) for s in sents if s["align"]]
    totals = defaultdict(int)
    for sid, lab in slab.items():
        totals[CLASS_OF.get(lab, "na")] += sents[sid]["words"]
    n_problem_sources = sum(1 for s in sources if s["verdict"] in ("naudotas_be_nuorodu", "naudotas_dalinai_be_nuorodu"))
    final = dict(vd["final"])
    final["risk"] = RISK_ID.get(final.get("risk_level"), final.get("risk_level"))
    from . import __version__
    rlang = i18n.normalize(lang or vd.get("lang") or cfg.report_lang)
    return dict(meta=seg["meta"], generated=datetime.now().strftime("%Y-%m-%d %H:%M"), version=__version__,
                lang=rlang, text_lang=vd.get("lang") or "lt",
                llm=vd.get("model"), final=final,
                stats=dict(stats, n_problem_sources=n_problem_sources, n_candidates=len(cands),
                           n_fetched=len(mt["sources"]), n_unselected=n_unselected),
                totals=totals, classes=CLASSES, class_of=CLASS_OF, problem_labels=sorted(PROBLEM_LABELS),
                sources=sources, others=others[:600], pages=page_list, doc=doc,
                locales=i18n.bundle(LOCALE_SECTIONS))


def template_path() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "plagcheck" / "template.html"
    return Path(__file__).resolve().parent / "template.html"


def render(data: dict) -> str:
    tpl = template_path().read_text(encoding="utf-8")
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = data["meta"].get("title") or i18n.t("report.untitled", data["lang"])
    return (tpl.replace("/*__DATA__*/null", js).replace("__TITLE__", html.escape(title))
            .replace("__LANG__", data["lang"]))


def run(cfg: Config, out: str | None = None, lang: str | None = None):
    data = build_data(cfg, lang)
    outp = Path(out) if out else cfg.path("index.html")
    outp.write_text(render(data), encoding="utf-8")
    log(f"Report: {outp.resolve()} ({outp.stat().st_size // 1024} KB)")
    return outp
