"""Stage 3: self-contained index.html report (no external dependencies, works offline)."""
from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from .config import Config
from .util import log, read_json, strip_accents
from .verify import PAIR_LABELS, SOURCE_VERDICTS, PROBLEM_LABELS

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
CLASSES = [
    ("copy", "Perimta pažodžiui be nuorodos", "#d03b3b"),
    ("para", "Perfrazuota be nuorodos", "#ec835a"),
    ("noquote", "Perimta pažodžiui be kabučių (autorius nurodytas)", "#fab219"),
    ("ok", "Cituota tinkamai arba su nuoroda", "#2a78d6"),
    ("na", "Ne plagiatas (vėlesnis šaltinis, bendrinė frazė, autoriaus darbas, atsitiktinis sutapimas)", "#a3a29b"),
]
USAGE_LT = {"pažodinis kopijavimas": "pažodinis perėmimas",
            "kopijavimas su nedideliais pakeitimais": "perėmimas su nedideliais pakeitimais",
            "perfrazavimas": "perfrazavimas",
            "struktūros / argumentų sekos perėmimas": "struktūros ir argumentų sekos perėmimas",
            "citavimas": "citavimas"}
STATUS_LT = {"ok": "tekstas gautas", "blocked": "prieiga užblokuota", "error": "nepavyko atsisiųsti"}
DATE_LT = {"before": "anksčiau už disertaciją", "after": "vėliau už disertaciją", "same_year": "tais pačiais metais",
           "unknown": "nenustatyta"}


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


def display_title(s: dict) -> str:
    """Replaces PDF metadata "titles" (e.g. "Microsoft Word - ...doc") with the real title."""
    t = (s.get("title") or "").strip()
    if s.get("kind") == "copy":
        return "Disertacijos tekstas (kopija internete)"
    if t and not JUNK_TITLE.search(t) and t != s.get("domain"):
        return t
    for alt in ((s.get("openalex") or {}).get("title"), (s.get("crossref") or {}).get("title")):
        if alt:
            return alt
    if s.get("ref_match"):
        return s["ref_match"][0][:160]
    if s.get("kind") == "copy":
        return "Disertacijos tekstas (kopija internete)"
    return f"Dokumentas svetainėje {s.get('domain', '')}" if t and JUNK_TITLE.search(t) else (t or s.get("domain", ""))


def date_text(d: str | None) -> str:
    if not d:
        return ""
    return f"{d[:4]} m." if d[5:] in ("01-01", "") else d


def build_data(cfg: Config) -> dict:
    seg = read_json(cfg.path("segments.json"))
    mt = read_json(cfg.path("matches.json"))
    vd = read_json(cfg.path("verdicts.json"))
    if not (seg and mt and vd):
        raise SystemExit("Missing data: run extract, search, fetch, align, verify.")
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
            pairs.append(dict(sid=p["sid"], page=p["page"], cat=p["cat"], cov=p["cov"], cont=p["cont"],
                              label=L.get("label", ""), note=L.get("note", ""), d=dh, s=sh, ev=p["evidence"]))
        cls_words = defaultdict(int)
        for p in pairs:
            cls_words[CLASS_OF.get(p["label"], "na")] += sents[p["sid"]]["words"]
        prob_words = sum(sents[p["sid"]]["words"] for p in pairs if p["label"] in PROBLEM_LABELS)
        sources.append(dict(
            id=s["sid"], url=s["url"], title=display_title(s), domain=s["domain"],
            status=s.get("status"), via=s.get("via"), evidence=s["evidence"], date=s.get("pub_date"),
            date_status=s.get("date_status"), date_note=s.get("date_note"), self=s.get("self"),
            in_refs=s.get("in_refs"), surnames=s.get("surnames"), mentions_author=s.get("mentions_author"),
            ref_match=s.get("ref_match") or [], n_pairs=s["n_pairs"], words=s["words"],
            pct=s["pct"], longest=s["longest"],
            pages=sorted({p["page"] for p in pairs}), verdict=v.get("verdict"),
            verdict_lt=SOURCE_VERDICTS.get(v.get("verdict"), v.get("verdict")), confidence=v.get("confidence"),
            usage=[USAGE_LT.get(u, u) for u in v.get("usage_types", [])], summary=v.get("summary", ""),
            prob_words=prob_words, prob_pct=round(100 * prob_words / max(stats["total_words"], 1), 2),
            cls_words=cls_words, pairs=pairs, local=bool(s.get("local"))))
    sources.sort(key=lambda x: (-x["prob_words"], -x["words"]))

    others = []
    rel_ids = {s["id"] for s in sources}
    for s in mt["sources"]:
        if s["sid"] in rel_ids:
            continue
        if s.get("kind") == "copy":
            reason = f"pačios disertacijos kopija internete ({s['n_pairs']} sak.) – neįskaičiuojama"
        elif s.get("kind") == "self":
            reason = f"paties autoriaus publikacija, parengta pagal disertaciją ({s['n_pairs']} sak.) – neįskaičiuojama"
        elif s.get("status") != "ok":
            reason = "teksto gauti nepavyko, o paieškos fragmentuose sutapimų per mažai"
        else:
            reason = "palyginus visą tekstą, reikšmingų sutapimų nerasta"
        others.append(dict(url=s["url"], title=display_title(s), domain=s["domain"],
                           status=s.get("status"), date=date_text(s.get("pub_date")), date_status=s.get("date_status"),
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
    return dict(meta=seg["meta"], generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                final=vd["final"], stats=dict(stats, n_problem_sources=n_problem_sources,
                                               n_candidates=len(cands), n_fetched=len(mt["sources"]),
                                               n_unselected=n_unselected),
                totals=totals, classes=CLASSES, labels=PAIR_LABELS, class_of=CLASS_OF, sources=sources,
                others=others[:600], pages=page_list, doc=doc, date_lt=DATE_LT, status_lt=STATUS_LT)


def run(cfg: Config, out: str | None = None):
    data = build_data(cfg)
    tpl = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = tpl.replace("/*__DATA__*/null", js).replace("__TITLE__", html.escape(data["meta"].get("title") or "Ataskaita"))
    outp = Path(out) if out else cfg.path("index.html")
    outp.write_text(page, encoding="utf-8")
    log(f"Report: {outp.resolve()} ({outp.stat().st_size // 1024} KB)")
    return outp
