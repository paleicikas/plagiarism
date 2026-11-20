"""Report exports: HTML (interactive), PDF (printable summary), CSV (one row per match pair), JSON, ZIP (all)."""
from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from pathlib import Path

from . import i18n, report
from .config import Config

FORMATS = ["html", "pdf", "csv", "json", "zip"]
_TAG = re.compile(r"<[^>]+>")


def _plain(s: str) -> str:
    return html.unescape(_TAG.sub("", s or ""))


def _title(s: dict, lang: str) -> str:
    return i18n.t(s["tk"]["k"], lang, **s["tk"]["p"]) if s.get("tk") else s["title"]


def _pct(x, lang, digits=1) -> str:
    s = f"{float(x or 0):.{digits}f}"
    return (s.replace(".", ",") if lang == "lt" else s) + " %"


def clean_data(data: dict) -> dict:
    """Report data without the UI texts and HTML markup – suitable for JSON / further processing."""
    lang = data["lang"]
    d = {k: v for k, v in data.items() if k not in ("locales", "classes", "class_of", "doc")}
    d["sources"] = []
    for s in data["sources"]:
        s2 = dict(s, title=_title(s, lang))
        s2.pop("tk", None)
        s2["pairs"] = [dict({k: v for k, v in p.items() if k not in ("d", "s")},
                            document=_plain(p["d"]), source=_plain(p["s"])) for p in s["pairs"]]
        d["sources"].append(s2)
    d["others"] = [dict({k: v for k, v in o.items() if k != "tk"}, title=_title(o, lang)) for o in data["others"]]
    d["document"] = [dict(id=x["i"], page=x["p"], text=x["t"], label=x["lab"], source=x["src"]) for x in data["doc"]]
    return d


def to_json(data: dict) -> str:
    return json.dumps(clean_data(data), ensure_ascii=False, indent=1)


def to_csv(data: dict) -> str:
    lang = data["lang"]
    T = lambda k, **p: i18n.t(k, lang, **p)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";" if lang in ("lt", "de", "fr", "es", "it", "pl", "ru", "uk") else ",")
    w.writerow([T(f"export.csv.{c}") for c in
                ("source_no", "source", "url", "verdict", "page", "sentence", "label", "verbatim_pct",
                 "content_pct", "document_text", "source_text")])
    for i, s in enumerate(data["sources"], 1):
        for p in s["pairs"]:
            w.writerow([i, _title(s, lang), s["url"], T(f"verdict.{s['verdict']}") if s.get("verdict") else "",
                        p["page"], p["sid"], T(f"label.{p['label']}") if p.get("label") else "",
                        round(100 * p["cov"]), round(100 * p["cont"]), _plain(p["d"]), _plain(p["s"])])
    return "\ufeff" + buf.getvalue()


PDF_CSS = """
body { font-size: 10pt; line-height: 1.35; color: #1d1d1b; }
h1 { font-size: 17pt; margin: 0 0 4pt 0; }
h2 { font-size: 12.5pt; margin: 14pt 0 5pt 0; color: #333; }
h3 { font-size: 10.5pt; margin: 9pt 0 3pt 0; }
.muted { color: #6b6b66; font-size: 8.5pt; }
.risk { font-weight: bold; }
.high { color: #c0262d; } .medium { color: #b26a00; } .low { color: #2f7d32; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 0.5pt solid #ccc; padding: 2pt 4pt; vertical-align: top; font-size: 8.5pt; text-align: left; }
th { border-bottom: 1pt solid #999; }
.pair td { font-size: 8pt; }
mark { background-color: #ffe08a; font-size: 8pt; }
.kv td { border: none; padding: 1pt 6pt 1pt 0; font-size: 9.5pt; }
"""


def to_pdf_html(data: dict, max_pairs: int = 12, max_sources: int = 40) -> str:
    lang = data["lang"]
    T = lambda k, **p: html.escape(i18n.t(k, lang, **p))
    E = html.escape
    m, st, f = data["meta"], data["stats"], data["final"]
    if f.get("auto"):
        f = dict(f, headline=i18n.t("auto.final_headline", lang, pct=_pct(st["problem_pct"], lang, 2)[:-2].strip()),
                 conclusion=i18n.t("auto.final_conclusion", lang), limitations=[i18n.t("auto.final_limitation", lang)])
    out = [f"<h1>{E(m.get('title') or i18n.t('report.untitled', lang))}</h1>",
           f"<p class='muted'>{E(m.get('author') or '')} · {T('report.doc_date', date=m.get('date') or '?')} · "
           f"{T('report.generated', date=data['generated'])}</p>"]
    risk = f.get("risk") or ""
    if risk:
        out.append(f"<p class='risk {E(risk)}'>{T('report.risk', level=i18n.t('risk.' + risk, lang))}</p>")
    if f.get("headline"):
        out.append(f"<p><b>{E(f['headline'])}</b></p>")
    for para in str(f.get("conclusion") or "").split("\n"):
        if para.strip():
            out.append(f"<p>{E(para)}</p>")
    for key, items in (("report.key_findings", f.get("key_findings")), ("report.limitations", f.get("limitations"))):
        if items:
            out.append(f"<h3>{T(key)}</h3><ul>" + "".join(f"<li>{E(str(x))}</li>" for x in items) + "</ul>")

    matched = 100 * sum(data["totals"].values()) / max(st["total_words"], 1)
    kpis = [(st["sentences"], T("report.kpi_sentences")),
            (_pct(matched, lang), T("report.kpi_matched")),
            (_pct(st["problem_pct"], lang), T("report.kpi_problem")),
            (st["n_problem_sources"], T("report.kpi_problem_sources")),
            (st["n_candidates"], T("report.kpi_candidates"))]
    out.append("<table class='kv'>" + "".join(f"<tr><td><b>{v}</b></td><td>{k}</td></tr>" for v, k in kpis) + "</table>")

    total = max(st["total_words"], 1)
    out.append(f"<h2>{T('report.composition_title')}</h2><table>")
    for cid, color in data["classes"]:
        w = data["totals"].get(cid, 0)
        out.append(f"<tr><td><span style='color:{color}'>■</span> {T('class.' + cid)}</td><td>{_pct(100 * w / total, lang)}</td></tr>")
    out.append("</table>")

    out.append(f"<h2>{T('report.tab_sources')}</h2><table><tr><th>#</th><th>{T('report.th_source')}</th>"
               f"<th>{T('export.verdict')}</th><th>{T('report.m_sentences')}</th><th>{T('report.m_problem')}</th></tr>")
    for i, s in enumerate(data["sources"][:max_sources], 1):
        v = T("verdict." + s["verdict"]) if s.get("verdict") else ""
        out.append(f"<tr><td>{i}</td><td>{E(_title(s, lang))}<br/><span class='muted'>{E(s['url'][:110])}</span></td>"
                   f"<td>{v}</td><td>{s['n_pairs']}</td><td>{_pct(s['prob_pct'], lang, 2)}</td></tr>")
    out.append("</table>")
    if len(data["sources"]) > max_sources:
        out.append(f"<p class='muted'>{T('export.more_sources', n=len(data['sources']) - max_sources)}</p>")

    problem = set(data["problem_labels"])
    shown = [s for s in data["sources"] if s["prob_words"] > 0][:max_sources]
    if shown:
        out.append(f"<h2>{T('export.pairs_title')}</h2>")
    for i, s in enumerate(shown, 1):
        out.append(f"<h3>{E(_title(s, lang))}</h3><p class='muted'>{E(s['url'])}</p>")
        if s.get("summary"):
            out.append(f"<p>{E(s['summary'])}</p>")
        pairs = [p for p in s["pairs"] if p["label"] in problem][:max_pairs]
        out.append(f"<table class='pair'><tr><th>{T('report.side_doc')}</th><th>{T('report.side_src')}</th></tr>")
        for p in pairs:
            lab = T("label." + p["label"]) if p.get("label") else ""
            out.append(f"<tr><td><span class='muted'>{T('report.doc_page', n=p['page'])} · {lab}</span><br/>{p['d']}</td>"
                       f"<td>{p['s']}</td></tr>")
        out.append("</table>")
        n_more = sum(1 for p in s["pairs"] if p["label"] in problem) - len(pairs)
        if n_more > 0:
            out.append(f"<p class='muted'>{T('export.more_pairs', n=n_more)}</p>")
    out.append(f"<p class='muted'>{T('export.pdf_note')}</p>")
    out.append(f"<p class='muted'>{T('report.footer', version=data.get('version', ''), date=data['generated'])}</p>")
    return "<body>" + "\n".join(out) + "</body>"


def to_pdf(data: dict) -> bytes:
    import pymupdf as fitz
    story = fitz.Story(html=to_pdf_html(data), user_css=PDF_CSS)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    page = fitz.paper_rect("a4")
    where = page + (42, 42, -42, -48)
    more = 1
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    doc = fitz.open("pdf", buf.getvalue())
    for i, pg in enumerate(doc, 1):
        pg.insert_text((page.width - 70, page.height - 24), f"{i} / {doc.page_count}", fontsize=8, color=(0.45, 0.45, 0.4))
    doc.set_metadata(dict(title=data["meta"].get("title") or "", author="Plagiarism", creator="Plagiarism"))
    try:
        doc.subset_fonts()
    except Exception:
        pass
    return doc.tobytes(garbage=3, deflate=True)


def export(cfg: Config, fmt: str, out: Path, lang: str | None = None) -> Path:
    data = report.build_data(cfg, lang)
    out = Path(out)
    if fmt == "html":
        out.write_text(report.render(data), encoding="utf-8")
    elif fmt == "json":
        out.write_text(to_json(data), encoding="utf-8")
    elif fmt == "csv":
        out.write_text(to_csv(data), encoding="utf-8", newline="")
    elif fmt == "pdf":
        out.write_bytes(to_pdf(data))
    elif fmt == "zip":
        base = re.sub(r"[^\w.-]+", "_", (data["meta"].get("title") or "report"))[:60] or "report"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{base}.html", report.render(data))
            z.writestr(f"{base}.pdf", to_pdf(data))
            z.writestr(f"{base}.csv", to_csv(data))
            z.writestr(f"{base}.json", to_json(data))
    else:
        raise ValueError(fmt)
    return out
