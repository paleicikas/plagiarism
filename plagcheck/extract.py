"""Stage 0: PDF -> clean sentences with page numbers.

Discards the table of contents (dotted lines), headings, tables, figure/table captions,
and page numbers; separately parses the reference list (for citation checks).
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from .config import Config, parse_pages
from .util import clean_text, split_sentences, word_count, write_json, log, tokens

BODY_START_RE = re.compile(r"^\s*(KEY DEFINITIONS|INTRODUCTION|ĮVADAS|PAGRINDINĖS SĄVOKOS|SĄVOKOS|"
                           r"CHAPTER\s+1|1\.\s+[A-Z])", re.M)
REF_START_RE = re.compile(r"^\s*(LIST OF REFERENCES|REFERENCES|BIBLIOGRAPHY|LITERATŪRA|"
                          r"LITERATŪROS SĄRAŠAS|NAUDOTA LITERATŪRA|ŠALTINIAI)\s*$", re.M)
REF_END_RE = re.compile(r"^\s*(ANNEX|ANNEXES|APPENDIX|APPENDICES|PRIEDAI|PRIEDAS|SANTRAUKA|SUMMARY|"
                        r"LIST OF PUBLICATIONS|CURRICULUM VITAE)\b", re.M)
TOC_RE = re.compile(r"(\.\s?){5,}|…{2,}")
HEADING_NUM_RE = re.compile(r"^(\d{1,2}\.)+(\d{1,2}\.?)?\s+\S")
CAPTION_RE = re.compile(r"^(Table|Figure|Fig\.|Lentelė|Pav\.|\d+\s*(lentelė|pav\.))\s*\d*", re.I)
CITE_RE = re.compile(
    r"\(\s*(?:see\s+|e\.g\.,?\s+|cf\.\s+)?[A-Z][\w'À-ſ\-]+(?:\s+(?:&|and)\s+[A-Z][\w'À-ſ\-]+)?"
    r"(?:\s+et\.?\s*al\.?)?,?\s*(?:\(?\d{4}[a-z]?|n\.d\.)"
    r"|[A-Z][\w'À-ſ\-]+(?:\s+(?:&|and)\s+[A-Z][\w'À-ſ\-]+)?(?:\s+et\.?\s*al\.?)?(?:'s)?\s*\(\s*\d{4}[a-z]?"
    r"|\(\s*\d{4}[a-z]?(?:,\s*p+\.\s*\d+)?\s*\)")


def _page_lines(page):
    """Returns lines with coordinates and block statistics."""
    d = page.get_text("dict")
    H = page.rect.height
    out = []
    for bi, b in enumerate(d.get("blocks", [])):
        if b.get("type") != 0:
            continue
        lines = []
        for ln in b.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in ln.get("spans", []))
            txt = clean_text(txt)
            if not txt:
                continue
            x0, y0, x1, y1 = ln["bbox"]
            lines.append(dict(text=txt, x0=x0, y0=y0, x1=x1, y1=y1, block=bi))
        if not lines:
            continue
        avg = sum(len(l["text"]) for l in lines) / len(lines)
        for l in lines:
            l["blk_n"] = len(lines)
            l["blk_avg"] = avg
        out.extend(lines)
    # header / footer (page numbers)
    res = []
    for l in out:
        if (l["y0"] > H * 0.88 or l["y1"] < H * 0.07) and (re.fullmatch(r"[\d\s]+", l["text"]) or len(l["text"]) < 4):
            continue
        res.append(l)
    res.sort(key=lambda l: (round(l["y0"] / 3), l["x0"]))
    return res


def _is_heading(t: str) -> bool:
    if len(t) > 140:
        return False
    letters = [c for c in t if c.isalpha()]
    caps = bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.85
    m = HEADING_NUM_RE.match(t)
    if m:
        rest = t[m.end() - 1:]
        multi = re.match(r"^\d{1,2}\.\d", t) is not None
        if (multi or caps) and ". " not in rest and not t.rstrip().endswith((",", ";", ":")):
            return True
        return False
    return caps and len(t) < 100


def detect_ranges(doc):
    texts = [doc[i].get_text() for i in range(len(doc))]
    body_start, ref_start, ref_end = None, None, None
    for i, t in enumerate(texts):
        head = "\n".join(t.strip().splitlines()[:6])
        if body_start is None and i > 2 and BODY_START_RE.search(head) and not TOC_RE.search(t[:2000]):
            body_start = i + 1
        if body_start and ref_start is None and i + 1 > body_start and REF_START_RE.search(head):
            ref_start = i + 1
        if ref_start and ref_end is None and i + 1 > ref_start and REF_END_RE.search(head):
            ref_end = i
    n = len(doc)
    body_start = body_start or 1
    body_end = (ref_start - 1) if ref_start else n
    refs = list(range(ref_start, (ref_end or n) + 1)) if ref_start else []
    return list(range(body_start, body_end + 1)), refs


def extract_body(doc, pages: list[int]):
    paragraphs = []      # list of dict(section, pieces=[(text, page)])
    section = ""
    cur = None
    skip_next_caption_line = False
    prev_x1 = 0.0

    def close():
        nonlocal cur
        if cur and cur["pieces"]:
            paragraphs.append(cur)
        cur = None

    for pno in pages:
        page = doc[pno - 1]
        lines = _page_lines(page)
        if not lines:
            continue
        prose_like = [l for l in lines if len(l["text"]) >= 50]
        margin = Counter(round(l["x0"]) for l in prose_like).most_common(1)[0][0] if prose_like else \
            min(round(l["x0"]) for l in lines)
        right = sorted(l["x1"] for l in prose_like)[int(len(prose_like) * 0.9)] if prose_like else page.rect.width
        for l in lines:
            t = l["text"]
            if skip_next_caption_line:
                skip_next_caption_line = False
                if l["x0"] < margin + 8 and len(t) < 120:
                    continue
            if TOC_RE.search(t):
                close(); continue
            if l["blk_n"] >= 3 and l["blk_avg"] < 35:      # table cells
                close(); continue
            if CAPTION_RE.match(t):
                close()
                skip_next_caption_line = l["x1"] >= right - 25
                continue
            indent = l["x0"] - margin
            if _is_heading(t):
                close()
                section = re.sub(r"\s+", " ", t)[:120]
                continue
            if indent > 45 or indent < -8:                    # centered text / table column
                close(); continue
            if cur is None or (indent >= 10 and prev_x1 < right - 30):
                close()
                cur = dict(section=section, pieces=[])
            cur["pieces"].append((t, pno))
            prev_x1 = l["x1"]
    close()
    return paragraphs


def _join_pieces(pieces):
    text, offs = "", []          # offs: (start_offset, page)
    for t, p in pieces:
        t = t.strip()
        if text.endswith("-") and len(text) > 1 and text[-2].isalpha() and t[:1].islower():
            pass                  # keep hyphen, no space ("non-\nmedia" -> "non-media")
        elif text:
            text += " "
        offs.append((len(text), p))
        text += t
    return re.sub(r"\s+", " ", text), offs


def _page_at(offs, pos):
    page = offs[0][1]
    for o, p in offs:
        if o <= pos:
            page = p
        else:
            break
    return page


def _quality(s: str) -> bool:
    letters = sum(c.isalpha() for c in s)
    if letters < 0.6 * max(len(s), 1):
        return False
    toks = s.split()
    nums = sum(bool(re.fullmatch(r"[\d.,%()\-–]+", w)) for w in toks)
    return nums / max(len(toks), 1) < 0.3


def parse_references(doc, pages):
    entries, cur = [], None
    for pno in pages:
        lines = _page_lines(doc[pno - 1])
        if not lines:
            continue
        xs = Counter(round(l["x0"]) for l in lines if len(l["text"]) > 30)
        margin = min(xs) if xs else min(round(l["x0"]) for l in lines)
        for l in lines:
            t = l["text"]
            if REF_START_RE.match(t) or re.fullmatch(r"\d+", t):
                continue
            starts = abs(l["x0"] - margin) < 6 and re.match(r"^[A-ZÀ-ſ][^\s]*", t)
            if starts and cur and not cur["raw"].rstrip().endswith(("&", ",")) or cur is None:
                if cur:
                    entries.append(cur)
                cur = dict(raw=t, page=pno)
            else:
                cur["raw"] += (" " if not cur["raw"].endswith("-") else "") + t
    if cur:
        entries.append(cur)
    out = []
    for e in entries:
        raw = re.sub(r"\s+", " ", e["raw"]).strip()
        ym = re.search(r"\((\d{4})[a-z]?\)|\b(\d{4})[a-z]?\.", raw)
        year = int(ym.group(1) or ym.group(2)) if ym else None
        head = raw[:ym.start()] if ym else raw[:120]
        surnames = re.findall(r"([A-ZÀ-ſ][\w'À-ſ\-]+(?: [a-z]+ [A-Z][\w\-]+)?),\s*(?:[A-Z]\.)", head)
        if not surnames:
            m = re.match(r"([A-Z][\w\-]+)", head)
            surnames = [m.group(1)] if m else []
        out.append(dict(raw=raw, year=year, surnames=surnames, page=e["page"]))
    return out


def run(cfg: Config):
    import pymupdf
    doc = pymupdf.open(cfg.pdf)
    auto_body, auto_refs = detect_ranges(doc)
    body = parse_pages(cfg.body_pages) or auto_body
    refs = parse_pages(cfg.ref_pages) if cfg.ref_pages else auto_refs
    log(f"PDF: {cfg.pdf}, {len(doc)} pages. Checking pages {body[0]}–{body[-1]}; references: "
        f"{(str(refs[0]) + '–' + str(refs[-1])) if refs else 'not found'}")

    meta = doc.metadata or {}
    doc_date = cfg.doc_date
    if not doc_date:
        m = re.match(r"D:(\d{4})(\d{2})(\d{2})", meta.get("creationDate", "") or "")
        doc_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
    first_page = clean_text(doc[0].get_text())
    title = cfg.doc_title
    if not title:     # longest run of consecutive upper-case lines that is not the university name
        runs, cur = [], []
        for ln in first_page.splitlines():
            ln = ln.strip()
            if ln and ln.isupper() and len(ln) > 3:
                cur.append(ln)
            elif cur:
                runs.append(" ".join(cur)); cur = []
        if cur:
            runs.append(" ".join(cur))
        runs = [r for r in runs if not re.search(r"UNIVERSIT|ACADEMY|INSTITUT|AKADEMIJA|DISSERTATION|DISERTACIJA", r)]
        title = max(runs, key=len) if runs else ""
    author = cfg.doc_author or next((ln.strip() for ln in first_page.splitlines()[:12] if re.fullmatch(
        r"[A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+(?: [A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+){1,2}", ln.strip())), "")
    markers = [m.strip() for m in cfg.author_markers.split(",") if m.strip()]
    if not markers and author:
        from .util import strip_accents
        markers = [author.split()[-1], strip_accents(author.split()[-1])]

    paragraphs = extract_body(doc, body)
    sentences = []
    for pi, para in enumerate(paragraphs):
        text, offs = _join_pieces(para["pieces"])
        pos = 0
        for s in split_sentences(text):
            k = text.find(s[:30], pos)
            k = pos if k < 0 else k
            pos = k + len(s)
            wc = word_count(s)
            if wc < 4:
                continue
            ok = _quality(s)
            sentences.append(dict(
                id=len(sentences), page=_page_at(offs, k), para=pi, section=para["section"], text=s,
                words=wc, cite=bool(CITE_RE.search(s)), quote='"' in s,
                search=ok and wc >= cfg.query_min_words, align=ok and wc >= 5))
    references = parse_references(doc, refs) if refs else []
    total_words = sum(s["words"] for s in sentences if s["align"])
    out = dict(meta=dict(pdf=str(cfg.pdf), pages=len(doc), body_pages=[body[0], body[-1]],
                         ref_pages=[refs[0], refs[-1]] if refs else None, title=title, author=author,
                         date=doc_date, extracted=datetime.now().isoformat(timespec="seconds"),
                         words=total_words, author_markers=markers),
               sentences=sentences, references=references)
    write_json(cfg.path("segments.json"), out)
    log(f"Sentences: {len(sentences)} (for search: {sum(s['search'] for s in sentences)}), words: {total_words}, "
        f"reference entries: {len(references)} -> {cfg.path('segments.json')}")
    return out
