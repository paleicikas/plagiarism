"""Stage 1c: compare each downloaded source against the ENTIRE dissertation text.

The search finds the source; here it is compared with every dissertation sentence, so copied /
paraphrased sentences that the search did not find directly are detected as well.

Categories (sentence level):
  identical   – verbatim match (≥90 % of words in common sequences)
  minor_edit  – rewritten by changing a few words (60–90 %)
  partial     – part of the sentence matches (35–60 %, long common sequence)
  paraphrase  – paraphrased: small share of word sequences, but ≥60 % of content words shared
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from .config import Config
from .errors import PipelineError
from .progress import activity
from .util import (KVCache, Progress, content_tokens, log, ngrams, read_json, split_sentences, strip_accents,
                   tokens, write_json)

CAT_ORDER = {"identical": 4, "minor_edit": 3, "partial": 2, "paraphrase": 1}
IDF: dict[str, float] = {}      # content word weights based on the dissertation (frequent topic words get lower weight)


def set_idf(sentence_tokens: list[list[str]]):
    import math
    df = Counter()
    for t in sentence_tokens:
        df.update(set(content_tokens(t)))
    n = max(len(sentence_tokens), 1)
    IDF.clear()
    IDF.update({w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()})


def compare(dt: list[str], st: list[str]) -> dict:
    sm = SequenceMatcher(None, dt, st, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 3]
    matched = sum(b.size for b in blocks)
    lcs = max((b.size for b in blocks), default=0)
    dc, sc = set(content_tokens(dt)), set(content_tokens(st))
    cont = len(dc & sc) / max(len(dc), 1)
    wsum = sum(IDF.get(w, 5.0) for w in dc)
    wcont = sum(IDF.get(w, 5.0) for w in dc & sc) / wsum if wsum else 0.0
    return dict(cov=round(matched / max(len(dt), 1), 3), lcs=lcs, cont=round(cont, 3), wcont=round(wcont, 3),
                ncont=len(dc))


def categorize(m: dict, words: int, relaxed: bool = False) -> str | None:
    cov, lcs, cont = m["cov"], m["lcs"], m["cont"]
    if relaxed and words >= 8 and cov < 0.35:
        # for already confirmed sources – more sensitive paraphrase detection (finally assessed in stage 2)
        wc = m.get("wcont", cont)
        if wc >= 0.45 and cont >= 0.45 and m["ncont"] >= 6 and (cov >= 0.08 or lcs >= 3):
            return "paraphrase"
    if words < 8:
        return "identical" if cov >= 0.9 and words >= 6 else None
    if cov >= 0.9:
        return "identical"
    if cov >= 0.6:
        return "minor_edit"
    if cov >= 0.35 and lcs >= 6:
        return "partial"
    wc = m.get("wcont", cont)
    if wc >= 0.6 and cont >= 0.6 and m["ncont"] >= 6 and (cov >= 0.12 or lcs >= 3):
        return "paraphrase"
    if wc >= 0.8 and m["ncont"] >= 8:
        return "paraphrase"
    return None


class SourceIndex:
    def __init__(self, text: str, max_sents: int = 30000):
        sents = split_sentences(text)
        # PDFs without punctuation can produce huge "sentences": cut them into ~40-word pieces
        out = []
        for s in sents:
            w = s.split()
            if len(w) > 80:
                for i in range(0, len(w), 40):
                    out.append(" ".join(w[i:i + 50]))
            else:
                out.append(s)
        self.sents = out[:max_sents]
        self.toks = [tokens(s) for s in self.sents]
        self.g4 = defaultdict(set)
        self.cw = defaultdict(set)
        for i, t in enumerate(self.toks):
            for g in ngrams(t, 4):
                self.g4[g].add(i)
            for w in set(content_tokens(t)):
                self.cw[w].add(i)
        self.df = {w: len(v) for w, v in self.cw.items()}

    def window(self, i: int, j: int) -> tuple[list[str], str]:
        i, j = max(i, 0), min(j, len(self.sents) - 1)
        t, s = [], []
        for k in range(i, j + 1):
            t += self.toks[k]
            s.append(self.sents[k])
        return t, " ".join(s)

    def best_match(self, dt: list[str], relaxed: bool = False):
        c4 = Counter()
        for g in ngrams(dt, 4):
            for i in self.g4.get(g, ()):
                c4[i] += 1
        dcont = set(content_tokens(dt))
        cc = Counter()
        if len(dcont) >= 6:
            n = len(self.sents)
            for w in dcont:
                post = self.cw.get(w)
                if post and len(post) < max(50, n * 0.05):    # skip very common words for speed
                    for i in post:
                        cc[i] += 1
        cands = [i for i, _ in c4.most_common(3)]
        need = max(4, int((0.35 if relaxed else 0.5) * len(dcont)))
        cands += [i for i, k in cc.most_common(3) if k >= need and i not in cands]
        best = None
        for i in cands:
            for a, b in ((i, i), (i - 1, i), (i, i + 1), (i - 1, i + 1)):
                st, stext = self.window(a, b)
                m = compare(dt, st)
                score = m["cov"] + 0.5 * m["cont"] - 0.02 * (b - a)
                if best is None or score > best[0]:
                    best = (score, m, stext)
        return best


def _surnames(src: dict) -> list[str]:
    out = []
    for a in src.get("authors") or []:
        a = re.sub(r"\(.*?\)", "", str(a)).strip()
        if not a:
            continue
        if "," in a:
            a = a.split(",")[0]
        else:
            a = a.split()[-1] if a.split() else a
        a = strip_accents(a).strip(". ")
        if len(a) > 2 and a.lower() not in ("admin", "editor", "staff", "guest", "anonymous"):
            out.append(a)
    return list(dict.fromkeys(out))[:12]


def run(cfg: Config):
    seg = read_json(cfg.path("segments.json"))
    if not seg:
        raise PipelineError("data.missing", "Run this first: python run.py extract", step="extract")
    sources = read_json(cfg.path("sources.json"), [])
    cands = {c["nurl"]: c for c in read_json(cfg.path("candidates.json"), [])}
    sents = seg["sentences"]
    doc_date = seg["meta"].get("date") or ""
    total_words = seg["meta"]["words"]
    d_toks = {s["id"]: tokens(s["text"]) for s in sents if s["align"]}
    set_idf(list(d_toks.values()))
    d_text_norm = strip_accents(" ".join(s["text"] for s in sents)).lower()
    refs = seg.get("references", [])
    ref_surnames = {strip_accents(n).lower() for r in refs for n in r.get("surnames", [])}
    markers = [strip_accents(m).lower() for m in seg["meta"].get("author_markers", [])]
    if not markers and seg["meta"].get("author"):
        markers = [strip_accents(seg["meta"]["author"].split()[-1]).lower()]
    title_norm = strip_accents(seg["meta"].get("title", "")).lower()
    ref_titles = reference_titles(refs)

    results = []
    n_align = sum(1 for s in sents if s["align"])
    # problematic sources from the previous assessment -> more sensitive paraphrase detection
    prev = read_json(cfg.path("verdicts.json"), {}) or {}
    focus = {k for k, v in (prev.get("verdicts") or {}).items()
             if v.get("verdict") in ("naudotas_be_nuorodu", "naudotas_dalinai_be_nuorodu")}
    if focus:
        log(f"More sensitive paraphrase detection for {len(focus)} already confirmed sources")
    overrides = read_json(Path(cfg.overrides_file), []) if cfg.overrides_file else []
    for src in sources:
        c = cands.get(src["nurl"]) or {}
        if c.get("ref_raw"):
            src["ref_raw"] = c["ref_raw"]
        for o in overrides or []:
            if o.get("match") and o["match"] in src.get("url", ""):
                if o.get("authors"):
                    src["authors"] = o["authors"]
                if o.get("title"):
                    src["title"] = o["title"]
                src["manual"] = {"date": o.get("date"), "note": o.get("note", "")}
                from .fetch import resolve_dates as _rd
                _rd(src, doc_date)
    prog = Progress(len(sources), "Comparing", phase="align.compare")
    prog.set(counters={"relevant": 0, "cat_identical": 0, "cat_minor_edit": 0, "cat_partial": 0, "cat_paraphrase": 0})
    for src in sources:
        prog.tick(current=activity("align", domain=src.get("domain", ""), title=(src.get("title") or "")[:100]))
        evidence = "fulltext"
        text = ""
        p = cfg.path("sources") / f"{src['sid']}.txt"
        if src.get("status") == "ok" and p.exists():
            text = p.read_text(encoding="utf-8")
        hits = (cands.get(src["nurl"]) or {}).get("hits", [])
        pairs = []
        def snippet_pair(h):
            s = sents[h["sid"]]
            if not s["align"]:
                return None
            dt = d_toks.get(s["id"], tokens(s["text"]))
            m = compare(dt, tokens(re.sub(r"\.{3}|…", " ", h["snippet"])))
            sn_t = tokens(h["snippet"])
            # snippet is shorter than the sentence: also assess by snippet coverage
            m["cov"] = round(max(m["cov"], h["cov"] * min(1.0, len(sn_t) / max(len(dt), 1))), 3)
            cat = categorize(m, s["words"])
            if cat and h["lcs"] >= 5:
                return dict(sid=s["id"], cat=cat, src_text=h["snippet"], evidence="snippet", **m)
            return None

        relaxed = src["sid"] in focus
        if text:
            evidence = "abstract" if src.get("partial") else "fulltext"
            idx = SourceIndex(text)
            for s in sents:
                if not s["align"]:
                    continue
                bm = idx.best_match(d_toks[s["id"]], relaxed)
                if not bm:
                    continue
                _, m, stext = bm
                cat = categorize(m, s["words"], relaxed)
                if cat:
                    pairs.append(dict(sid=s["id"], cat=cat, src_text=stext[:1500], evidence=evidence, **m))
            # Google saw text that is missing from the downloaded version (abstract, changed page) – add it
            have = {p["sid"] for p in pairs}
            for h in hits:
                if h["sid"] not in have and (src.get("partial") or h["lcs"] >= 8):
                    sp = snippet_pair(h)
                    if sp:
                        pairs.append(sp)
        else:
            evidence = "snippet"
            for h in hits:
                sp = snippet_pair(h)
                if sp:
                    pairs.append(sp)
        low = strip_accents(text[:400000]).lower()
        self_flag = bool(text) and (any(mk in low for mk in markers) or (len(title_norm) > 20 and title_norm in low))
        surn = _surnames(src) or (src.get("crossref") or {}).get("authors", [])[:12]
        # reference list entry whose title matches the source title / beginning
        ref_match = match_references(src, text, ref_titles) if pairs else []
        if src.get("ref_raw") and not any(r_["raw"] == src["ref_raw"] for r_ in ref_match):
            ref_match = [r_ for r_ in refs if r_["raw"] == src["ref_raw"]][:1] + ref_match
        if not surn and ref_match:
            surn = list(dict.fromkeys(n for r_ in ref_match for n in r_["surnames"]))[:12]
        surn_l = [strip_accents(x).lower() for x in surn]
        in_refs = [x for x in surn_l if x in ref_surnames]
        in_text = [x for x in surn_l if re.search(r"\b" + re.escape(x) + r"\b", d_text_norm)]
        for pr in pairs:
            s = sents[pr["sid"]]
            near = " ".join(x["text"] for x in sents[max(0, pr["sid"] - 3): pr["sid"] + 2])
            near = strip_accents(near).lower()
            pr.update(page=s["page"], cite=s["cite"], quote=s["quote"],
                      cited_near=any(re.search(r"\b" + re.escape(x) + r"\b", near) for x in surn_l))
        n_words = sum(sents[p["sid"]]["words"] for p in pairs)
        strong = [p for p in pairs if p["cat"] in ("identical", "minor_edit")]
        relevant = (len(pairs) >= 2 and (len(strong) >= 1 or len(pairs) >= 4)) or \
                   any(p["cat"] in ("identical", "minor_edit") and sents[p["sid"]]["words"] >= 12 for p in pairs)
        # The dissertation itself (a copy in a repository) and the author's own publications based on the dissertation
        # are not external sources – they are shown separately and not counted in the match extent.
        if len(pairs) >= 0.5 * n_align:
            kind = "copy"
        elif self_flag and len(pairs) >= 25:
            kind = "self"
        else:
            kind = "normal"
        r = dict(src)
        bad_title = re.search(r"checking your browser|not a bot|just a moment|access denied|attention required|"
                              r"verify you are human|captcha", (r.get("title") or ""), re.I)
        if bad_title or not r.get("title"):
            r["title"] = (r.get("openalex") or {}).get("title") or (r.get("crossref") or {}).get("title") or r["domain"]
        r.update(evidence=evidence, pairs=sorted(pairs, key=lambda p: p["sid"]), n_pairs=len(pairs),
                 n_strong=len(strong), words=n_words, pct=round(100 * n_words / max(total_words, 1), 2),
                 cats=dict(Counter(p["cat"] for p in pairs)), self=kind == "self", kind=kind,
                 mentions_author=self_flag and kind == "normal", surnames=surn,
                 ref_match=[r_["raw"] for r_ in ref_match], authors_known=bool(surn),
                 in_refs=in_refs, in_text=in_text, relevant=relevant and kind == "normal", n_search_hits=len(hits))
        r["passages"] = passages(r["pairs"], sents)
        r["longest"] = max((ps["words"] for ps in r["passages"]), default=0)
        results.append(r)
        if r["relevant"]:
            prog.count("relevant")
            for p in pairs:
                prog.count("cat_" + p["cat"])

    # Wayback first capture for relevant sources without a reliable "before" date
    cache = KVCache(cfg.path("cache_fetch.sqlite"))
    need = [r for r in results if r["relevant"] and r.get("date_status") != "before" and not r.get("local")]
    if need and cfg.use_wayback:
        from .fetch import wayback_first_capture, resolve_dates
        log(f"Checking archive (Wayback) dates for {len(need)} sources...")
        pw = Progress(len(need), "Archive dates", phase="align.wayback")
        for r in need:
            pw.tick(current=activity("wayback", domain=r.get("domain", "")))
            key = "cdx|" + r["url"]
            wb = cache.get(key)
            if wb is None:
                wb = wayback_first_capture(r["url"]) or ""
                cache.set(key, wb)
            if wb:
                r["wayback_first"] = wb
                resolve_dates(r, doc_date)

    # sentence -> sources map (best source per sentence)
    smap = defaultdict(list)
    for r in results:
        if not r["relevant"]:
            continue
        for p in r["pairs"]:
            smap[p["sid"]].append(dict(src=r["sid"], cat=p["cat"], cov=p["cov"], cont=p["cont"]))
    for sid in smap:
        smap[sid].sort(key=lambda x: (-CAT_ORDER[x["cat"]], -x["cov"]))
    results.sort(key=lambda r: (-int(r["relevant"]), -r["words"]))
    rel = [r for r in results if r["relevant"]]
    covered_words = sum(sents[sid]["words"] for sid in smap)
    out = dict(sources=results, sentence_map=smap,
               stats=dict(total_words=total_words, sentences=len([s for s in sents if s["align"]]),
                          matched_sentences=len(smap), matched_words=covered_words,
                          matched_pct=round(100 * covered_words / max(total_words, 1), 2),
                          relevant_sources=len(rel)))
    write_json(cfg.path("matches.json"), out)
    for kind, lt in (("copy", "dissertation copies"), ("self", "author's own publications")):
        ks = [r for r in results if r.get("kind") == kind]
        if ks:
            log(f"Separated ({lt}, not counted): " + ", ".join(r["domain"] for r in ks))
    log(f"Relevant sources: {len(rel)}; matching sentences: {len(smap)} "
        f"({out['stats']['matched_pct']} % of text) -> {cfg.path('matches.json')}")
    return out


def _norm(t: str) -> str:
    return " ".join(tokens(t or ""))


def reference_titles(refs: list[dict]) -> list[dict]:
    out = []
    for r in refs:
        m = re.search(r"\(\s*(?:\d{4}[a-z]?|n\.d\.)\s*\)\.?\s*(.+?)(?:[.?!]\s|$)", r["raw"])
        t = _norm(m.group(1)) if m else ""
        if len(t) >= 20:
            out.append(dict(r, title_norm=t))
    return out


def match_references(src: dict, text: str, ref_titles: list[dict]) -> list[dict]:
    """Whether this source is in the dissertation's reference list – by title (not only by author surnames).
    Short / generic titles ("The theory of planned behavior") are compared only with the source title."""
    from rapidfuzz import fuzz
    titles = [_norm(t) for t in (src.get("title"), (src.get("openalex") or {}).get("title"),
                                 (src.get("crossref") or {}).get("title")) if t]
    head = _norm(text[:3000])
    found = []
    for r in ref_titles:
        t = r["title_norm"]
        hit = any(fuzz.ratio(t, tt) >= 90 or (len(t) >= 35 and fuzz.partial_ratio(t, tt) >= 93 and len(tt) >= 0.6 * len(t))
                  for tt in titles)
        if not hit and len(t) >= 40 and head and len(t) <= len(head):
            hit = fuzz.partial_ratio(t, head) >= 93
        if hit:
            found.append(r)
    found.sort(key=lambda r: -len(r["title_norm"]))
    return found[:2]


def passages(pairs, sents, gap: int = 1):
    out, cur = [], None
    for p in pairs:
        if cur and p["sid"] - cur["end"] <= gap + 1:
            cur["end"] = p["sid"]
            cur["sids"].append(p["sid"])
        else:
            if cur:
                out.append(cur)
            cur = dict(start=p["sid"], end=p["sid"], sids=[p["sid"]])
    if cur:
        out.append(cur)
    for ps in out:
        ps["words"] = sum(sents[s]["words"] for s in ps["sids"])
        ps["pages"] = sorted({sents[s]["page"] for s in ps["sids"]})
    return sorted(out, key=lambda x: -x["words"])
