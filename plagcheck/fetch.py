"""Stage 1b: download selected sources, extract text, determine publication date.

Fallbacks when a page is inaccessible: Europe PMC (PMC articles), Crossref + Unpaywall (by DOI
or title), Wayback Machine archive. If everything fails, the search snippets remain.
"""
from __future__ import annotations

import io
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import requests

from .config import Config
from .util import KVCache, Progress, YEAR_RE, clean_text, log, norm_url, read_json, sha1, write_json, domain

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"}
BLOCK_MARKERS = ("just a moment", "checking your browser", "captcha", "access denied", "enable javascript",
                 "are you a robot", "request unsuccessful", "attention required", "verify you are human",
                 "unusual traffic", "cf-browser-verification")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>&?#]+)", re.I)
META_DATE_NAMES = ["citation_publication_date", "citation_date", "citation_online_date", "dc.date",
                   "dc.date.issued", "dcterms.issued", "prism.publicationdate", "article:published_time",
                   "og:published_time", "datepublished", "date", "pubdate", "publish_date", "sailthru.date",
                   "citation_year", "dc.date.created", "parsely-pub-date"]
try:   # Europe PMC returns XML – html.parser extracts it fine, the warning is unnecessary
    import warnings
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except Exception:
    pass
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ---------------------------------------------------------------- helpers

def _year_from(s: str | None) -> int | None:
    if not s:
        return None
    m = YEAR_RE.search(str(s))
    return int(m.group(1)) if m else None


def _date_from(s: str | None) -> str | None:
    if not s:
        return None
    s = str(s)
    m = re.search(r"(19|20)\d{2}-\d{2}-\d{2}", s)
    if m:
        return m.group(0)
    m = re.search(r"(19|20)\d{2}[/-]\d{1,2}", s)
    if m:
        y, mo = re.split(r"[/-]", m.group(0))
        return f"{y}-{int(mo):02d}-01"
    for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s.strip()[:20], fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    y = _year_from(s)
    return f"{y}-01-01" if y else None


def _is_blocked(status: int, text: str) -> bool:
    low = text[:3000].lower()
    return status in (401, 403, 429, 503) or (len(text) < 2500 and any(m in low for m in BLOCK_MARKERS))


def _pdf_text(data: bytes) -> tuple[str, dict]:
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    parts = []
    for i, p in enumerate(doc):
        if i >= 400:
            break
        parts.append(p.get_text())
    meta = doc.metadata or {}
    text = "\n".join(parts)
    text = re.sub(r"-\n(?=[a-z])", "", text)   # hyphenation
    return text, meta


def _html_text(html: str, url: str) -> tuple[str, dict]:
    meta = {"dates": [], "title": "", "authors": [], "doi": None}
    text = ""
    try:
        import trafilatura
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True,
                                   favor_recall=True) or ""
        md = trafilatura.extract_metadata(html)
        if md:
            if md.date:
                meta["dates"].append(("trafilatura", md.date))
            meta["title"] = md.title or ""
            if md.author:
                meta["authors"] = [a.strip() for a in re.split(r";|,| and ", md.author) if a.strip()][:10]
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for m in soup.find_all("meta"):
            name = (m.get("name") or m.get("property") or m.get("itemprop") or "").lower()
            content = m.get("content") or ""
            if name in META_DATE_NAMES and content:
                meta["dates"].append((name, content))
            if name == "citation_author" and content:
                meta["authors"].append(content)
            if name in ("citation_doi", "dc.identifier", "prism.doi") and "10." in content:
                meta["doi"] = DOI_RE.search(content).group(1) if DOI_RE.search(content) else None
            if name in ("citation_title", "og:title") and content and not meta["title"]:
                meta["title"] = content
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                j = json.loads(s.string or "{}")
                for o in (j if isinstance(j, list) else [j]):
                    if isinstance(o, dict):
                        for k in ("datePublished", "dateCreated"):
                            if o.get(k):
                                meta["dates"].append(("jsonld:" + k, o[k]))
            except Exception:
                pass
        if len(text) < 500:
            for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
                t.decompose()
            text2 = soup.get_text("\n")
            if len(text2) > len(text):
                text = text2
        if not meta["title"] and soup.title:
            meta["title"] = soup.title.get_text(strip=True)
    except Exception:
        pass
    if not meta["doi"]:
        m = DOI_RE.search(html[:200000])
        meta["doi"] = m.group(1).rstrip(".;)") if m else None
    return text, meta


def _get(url, timeout, **kw):
    return SESSION.get(url, timeout=timeout, allow_redirects=True, **kw)


def _parse_response(r) -> dict:
    ctype = r.headers.get("content-type", "").lower()
    data = r.content
    res = dict(status=r.status_code, final_url=r.url, ctype=ctype, text="", title="", dates=[], authors=[],
               doi=None)
    if data[:5] == b"%PDF-" or "application/pdf" in ctype:
        try:
            text, meta = _pdf_text(data)
            res["text"] = text
            res["title"] = meta.get("title") or ""
            if meta.get("creationDate"):
                res["dates"].append(("pdf:creationDate", meta["creationDate"][2:10]))
            m = DOI_RE.search(text[:5000])
            res["doi"] = m.group(1).rstrip(".;)") if m else None
            res["kind"] = "pdf"
        except Exception as e:
            res["error"] = f"pdf: {e}"
    else:
        html = r.text if r.encoding else data.decode("utf-8", "replace")
        text, meta = _html_text(html, r.url)
        res.update(text=text, title=meta["title"], dates=meta["dates"], authors=meta["authors"], doi=meta["doi"],
                   kind="html")
        if _is_blocked(r.status_code, text or html):
            res["blocked"] = True
    if r.status_code >= 400:
        res["blocked"] = True
    return res


def download(url: str, timeout: int) -> dict:
    return _parse_response(_get(url, timeout))


# ---------------------------------------------------------------- fallbacks

def crossref_doi(doi: str, timeout=20) -> dict | None:
    try:
        r = _get(f"https://api.crossref.org/works/{quote(doi)}", timeout)
        if r.status_code != 200:
            return None
        m = r.json()["message"]
        return _crossref_parse(m)
    except Exception:
        return None


def _crossref_parse(m: dict) -> dict:
    dates = []
    for k in ("published-print", "published-online", "issued", "created"):
        dp = (m.get(k) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            p = dp[0]
            dates.append(f"{p[0]:04d}-{(p[1] if len(p) > 1 else 1):02d}-{(p[2] if len(p) > 2 else 1):02d}")
        if k == "issued" and dates:
            break          # "created" only if nothing better
    date = min(dates) if dates else None
    authors = [f"{a.get('family', '')}" for a in m.get("author", []) if a.get("family")]
    return dict(doi=m.get("DOI"), date=date, title=(m.get("title") or [""])[0], authors=authors,
                container=(m.get("container-title") or [""])[0])


def crossref_title(title: str, timeout=20) -> dict | None:
    from rapidfuzz import fuzz
    if not title or len(title) < 15:
        return None
    try:
        r = _get("https://api.crossref.org/works", timeout, params={"query.bibliographic": title, "rows": 3})
        for m in r.json()["message"]["items"]:
            t = (m.get("title") or [""])[0]
            if fuzz.token_set_ratio(t.lower(), title.lower()) >= 90:
                return _crossref_parse(m)
    except Exception:
        return None
    return None


def unpaywall_pdf(doi: str, email: str, timeout=20) -> str | None:
    if not email:
        return None
    try:
        r = _get(f"https://api.unpaywall.org/v2/{quote(doi)}", timeout, params={"email": email})
        if r.status_code != 200:
            return None
        j = r.json()
        locs = [j.get("best_oa_location")] + (j.get("oa_locations") or [])
        for loc in locs:
            if loc and (loc.get("url_for_pdf") or loc.get("url")):
                return loc.get("url_for_pdf") or loc.get("url")
    except Exception:
        return None
    return None


def europepmc_text(url: str, timeout=30) -> str | None:
    m = re.search(r"(PMC\d+)", url or "", re.I)
    if not m:
        return None
    try:
        r = _get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{m.group(1).upper()}/fullTextXML", timeout)
        if r.status_code == 200 and len(r.text) > 1000:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            return soup.get_text(" ")
    except Exception:
        return None
    return None


_ARCHIVE_LOCK = __import__("threading").Lock()
_ARCHIVE_LAST = [0.0]


def _archive_get(url, timeout, **kw):
    """archive.org rate-limits requests (HTTP 429): at most once every 1.5 s, retried after waiting."""
    import time as _t
    for attempt in range(4):
        with _ARCHIVE_LOCK:
            wait = _ARCHIVE_LAST[0] + 1.5 - _t.time()
            if wait > 0:
                _t.sleep(wait)
            _ARCHIVE_LAST[0] = _t.time()
        r = _get(url, timeout, **kw)
        if r.status_code != 429:
            return r
        _t.sleep(10 * (attempt + 1))
    return r


def wayback_fetch(url: str, near: str, timeout=40) -> dict | None:
    """Archived copy closest to the dissertation date."""
    ts = (near or "2015-01-01").replace("-", "")
    try:
        r = _archive_get("https://archive.org/wayback/available", 25, params={"url": url, "timestamp": ts})
        snap = (r.json().get("archived_snapshots") or {}).get("closest")
        if not snap or not snap.get("available"):
            return None
        sts = snap["timestamp"]
        r2 = _archive_get(f"https://web.archive.org/web/{sts}id_/{url}", timeout)
        res = _parse_response(r2)
        res["wayback_ts"] = sts
        return res
    except Exception:
        return None


def wayback_first_capture(url: str, timeout=30) -> str | None:
    try:
        r = _archive_get("https://web.archive.org/cdx/search/cdx", timeout,
                         params={"url": url, "limit": 1, "output": "json", "fl": "timestamp"})
        rows = r.json()
        if len(rows) > 1:
            t = rows[1][0]
            return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    except Exception:
        return None
    return None


# ---------------------------------------------------------------- OpenAlex / Europe PMC

def _oa_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = []
    for w, idxs in inv.items():
        for i in idxs:
            pos.append((i, w))
    return " ".join(w for _, w in sorted(pos))


def openalex_lookup(doi=None, pmid=None, pmcid=None, title=None, email="", timeout=25) -> dict | None:
    """Reliable publication date, authors, abstract and open-access PDF link."""
    from rapidfuzz import fuzz
    params = {"mailto": email} if email else {}
    try:
        if doi:
            r = _get(f"https://api.openalex.org/works/doi:{quote(doi)}", timeout, params=params)
            via = "openalex"
        elif pmid:
            r = _get(f"https://api.openalex.org/works/pmid:{pmid}", timeout, params=params)
            via = "openalex"
        elif pmcid:
            r = _get(f"https://api.openalex.org/works/pmcid:{pmcid}", timeout, params=params)
            via = "openalex"
        elif title and len(title) >= 20:
            r = _get("https://api.openalex.org/works", timeout,
                     params=dict(params, search=title[:250], **{"per-page": 3}))
            via = "openalex_title"
        else:
            return None
        if r.status_code != 200:
            return None
        j = r.json()
        if via == "openalex_title":
            hit = None
            for w in j.get("results", []):
                if fuzz.token_set_ratio((w.get("title") or "").lower(), title.lower()) >= 92:
                    hit = w
                    break
            if not hit:
                return None
            j = hit
        loc = j.get("best_oa_location") or {}
        ids = j.get("ids") or {}
        return dict(via=via, date=j.get("publication_date"), title=j.get("title") or "",
                    doi=(j.get("doi") or "").replace("https://doi.org/", "") or None,
                    authors=[(a.get("author") or {}).get("display_name", "") for a in j.get("authorships", [])][:12],
                    abstract=_oa_abstract(j.get("abstract_inverted_index")),
                    pdf=loc.get("pdf_url") or (j.get("open_access") or {}).get("oa_url"),
                    pmcid=(ids.get("pmcid") or "").rsplit("/", 1)[-1] or None)
    except Exception:
        return None


def europepmc_by_id(pmid=None, doi=None, timeout=25) -> dict | None:
    """Europe PMC: PMC ID (for full text) and abstract by PMID or DOI."""
    q = f"EXT_ID:{pmid} AND SRC:MED" if pmid else f'DOI:"{doi}"' if doi else None
    if not q:
        return None
    try:
        r = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", timeout,
                 params={"query": q, "format": "json", "resultType": "core", "pageSize": 1})
        res = (r.json().get("resultList") or {}).get("result") or []
        if not res:
            return None
        o = res[0]
        return dict(pmcid=o.get("pmcid"), abstract=re.sub(r"<[^>]+>", " ", o.get("abstractText") or ""),
                    date=o.get("firstPublicationDate"), title=o.get("title"))
    except Exception:
        return None


def title_from_url(url: str) -> str:
    """ResearchGate and similar URLs often contain the title: /publication/123_Title_words."""
    path = unquote(urlsplit(url).path)
    m = re.search(r"/(?:publication|figure|post)/\d+_([^/]+)", path)
    if m:
        return m.group(1).replace("_", " ").replace("-", " ")
    return ""


# ---------------------------------------------------------------- main per-source logic

DATE_WEIGHT = {"crossref": 3, "openalex": 3, "europepmc": 3, "crossref_title": 2, "openalex_title": 2,
               "url": 2, "google": 2, "failo pavadinimas": 2, "trafilatura": 1, "pdf:creationDate": 1}
DATE_SRC_LT = {"crossref": "Crossref pagal DOI", "openalex": "OpenAlex", "europepmc": "Europe PMC",
               "crossref_title": "Crossref pagal pavadinimą", "openalex_title": "OpenAlex pagal pavadinimą",
               "url": "URL", "google": "Google paieška", "trafilatura": "puslapio data (nepatikima)",
               "pdf:creationDate": "PDF sukūrimo data (nepatikima)", "failo pavadinimas": "failo pavadinimas",
               "rankinė patikra": "rankinė patikra"}


def resolve_dates(src: dict, doc_date: str) -> None:
    """Choose the publication date and determine its relation to the dissertation date.

    A false "later" would hide real plagiarism, so the EARLIEST reliable date (weight ≥2) is taken,
    and a source is not considered "later" based on unreliable signals alone (page update date, PDF creation date).
    """
    cands = []   # (date, label, weight)
    for key in ("crossref", "openalex", "europepmc_meta"):
        o = src.get(key) or {}
        if o.get("date"):
            lab = o.get("via") or ("crossref" if key == "crossref" else "europepmc" if key == "europepmc_meta" else key)
            if key == "crossref" and o.get("by_title"):
                lab = "crossref_title"
            cands.append((_date_from(o["date"]), lab, DATE_WEIGHT.get(lab, 2)))
    if (src.get("manual") or {}).get("date"):
        cands.append((_date_from(src["manual"]["date"]), "rankinė patikra", 4))
    for name, val in src.get("meta_dates", []):
        d = _date_from(val)
        if d:
            if name == "trafilatura":
                w = 1
            elif name.startswith(("citation_", "prism", "dc")):
                w = 3
            else:
                w = 2
            cands.append((d, name, w))
    m = re.search(r"/((?:19|20)\d{2})/(\d{2})/(\d{2})?", src.get("url", ""))
    if m:
        cands.append((f"{m.group(1)}-{m.group(2)}-{m.group(3) or '01'}", "url", 2))
    for d in src.get("search_dates", []):
        dd = _date_from(d)
        if dd:
            cands.append((dd, "google", 2))
    if src.get("pdf_created"):
        cands.append((src["pdf_created"], "pdf:creationDate", 1))
    if src.get("local_year"):
        cands.append((f"{src['local_year']}-01-01", "failo pavadinimas", 2))
    # dates close to today are usually the page update / crawl date
    from datetime import timedelta
    recent = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    cands = [c for c in cands if c[0] and "1950" <= c[0][:4] <= str(datetime.now().year)
             and not (c[2] < 3 and c[0] >= recent)]
    src["date_candidates"] = cands
    strong = [c for c in cands if c[2] >= 2]
    weak = [c for c in cands if c[2] < 2]
    key = lambda c: (c[0][:4], -c[2], c[0])      # earliest year; within the same year – the most reliable source
    best = min(strong, key=key) if strong else (min(weak, key=key) if weak else None)
    src["pub_date"] = best[0] if best else None
    src["pub_date_src"] = best[1] if best else None
    src["date_reliable"] = bool(best and best[2] >= 2)
    wb = src.get("wayback_first")
    dd = doc_date or "9999"
    label = f"{best[0]} ({DATE_SRC_LT.get(best[1], best[1])})" if best else "Data nenustatyta"
    if (src.get("manual") or {}).get("note"):
        label += f" – {src['manual']['note']}"
    if wb and wb < dd:
        src["date_status"] = "before"
        src["date_note"] = f"Archyve (Wayback) užfiksuota {wb}, t. y. anksčiau nei disertacija"
        if best and best[0] > dd:
            src["date_note"] += f"; nurodyta data {label}"
        return
    if not best:
        status = "unknown"
    elif best[0][:4] < dd[:4]:
        status = "before"
    elif best[0][:4] > dd[:4]:
        status = "after"
    elif best[0][5:] != "01-01" and best[0] < dd:
        status = "before"
    else:
        status = "same_year"
    if status == "after" and not src["date_reliable"]:
        status = "unknown"
        label += " – nepatikima, todėl laikoma nežinoma"
    elif status == "after" and best[2] < 3 and any(c[0] < dd for c in cands):
        earlier = min(c for c in cands if c[0] < dd)
        status = "unknown"
        label += f"; prieštarauja kitas požymis {earlier[0]} ({DATE_SRC_LT.get(earlier[1], earlier[1])}) – laikoma nežinoma"
    src["date_status"] = status
    src["date_note"] = label


FETCH_VERSION = 2


def _ids_from_url(url: str) -> dict:
    u = unquote(url)
    out = {}
    m = re.search(r"(PMC\d+)", u, re.I)
    if m:
        out["pmcid"] = m.group(1).upper()
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", u) or re.search(r"ncbi\.nlm\.nih\.gov/pubmed/(\d+)", u)
    if m:
        out["pmid"] = m.group(1)
    m = DOI_RE.search(u)
    if m:
        out["doi"] = m.group(1).rstrip(".;)")
    return out


def enrich_meta(src: dict, cfg: Config, page_ok: bool) -> None:
    """Crossref + OpenAlex + Europe PMC metadata (dates, authors, DOI)."""
    ids = _ids_from_url(src["url"])
    src["doi"] = src.get("doi") or ids.get("doi")
    if src.get("doi") and not src.get("crossref"):
        cr = crossref_doi(src["doi"])
        if cr:
            src["crossref"] = cr
    oa = openalex_lookup(doi=src.get("doi"), pmid=ids.get("pmid"), pmcid=ids.get("pmcid"),
                         email=cfg.unpaywall_email) if (src.get("doi") or ids.get("pmid") or ids.get("pmcid")) else None
    if not oa:
        t = title_from_url(src["url"]) or (src.get("title") if not page_ok else "")
        t = re.sub(r"\s*[|\-–]\s*(ResearchGate|ScienceDirect|PubMed|Emerald|JSTOR|Springer.*|Wiley.*|Academia\.edu|"
                   r"Scribd|Taylor.*|SAGE.*)\s*$", "", t or "", flags=re.I)
        if t and len(t) >= 20:
            oa = openalex_lookup(title=t, email=cfg.unpaywall_email)
            if not oa and not src.get("crossref"):
                cr = crossref_title(t)
                if cr:
                    cr["by_title"] = True
                    src["crossref"] = cr
    if oa:
        src["openalex"] = {k: v for k, v in oa.items() if k != "abstract"}
        src["_abstract"] = oa.get("abstract") or ""
        src["doi"] = src.get("doi") or oa.get("doi")
        if oa.get("pmcid"):
            ids["pmcid"] = oa["pmcid"]
    if ids.get("pmid") and not src.get("europepmc_meta"):
        ep = europepmc_by_id(pmid=ids["pmid"])
        if ep:
            src["europepmc_meta"] = {"date": ep.get("date"), "pmcid": ep.get("pmcid")}
            src["_abstract"] = src.get("_abstract") or ep.get("abstract") or ""
            if ep.get("pmcid"):
                ids["pmcid"] = ep["pmcid"]
    src["_pmcid"] = ids.get("pmcid")
    authors = (src.get("crossref") or {}).get("authors") or (src.get("openalex") or {}).get("authors")
    if authors and not src.get("authors"):
        src["authors"] = authors
    src["meta_v"] = FETCH_VERSION


def fetch_one(cand: dict, cfg: Config, doc_date: str) -> dict:
    url = cand["url"]
    src = dict(sid=sha1(cand["nurl"])[:16], url=url, nurl=cand["nurl"], domain=cand["domain"],
               title=cand.get("title", ""), search_dates=cand.get("dates", []), status="error", via="direct",
               meta_dates=[], authors=[], doi=None, fetch_v=FETCH_VERSION)
    attempts = []
    res = None
    try:
        res = download(url, cfg.fetch_timeout)
        attempts.append(f"direct:{res['status']}{' blocked' if res.get('blocked') else ''}")
    except Exception as e:
        attempts.append(f"direct:{type(e).__name__}")

    def good(r):
        return bool(r) and not r.get("blocked") and len((r.get("text") or "").strip()) >= 600

    if res:
        src["meta_dates"] = [d for d in res.get("dates", []) if d[0] != "pdf:creationDate"]
        src["authors"] = res.get("authors", [])
        src["doi"] = res.get("doi")
        if res.get("title") and len(res["title"]) > 5 and good(res):
            src["title"] = clean_text(res["title"])
        for n, v in res.get("dates", []):
            if n == "pdf:creationDate":
                src["pdf_created"] = _date_from(v)

    enrich_meta(src, cfg, good(res))

    # 1) Europe PMC full text (PMC articles)
    if not good(res) and src.get("_pmcid"):
        t = europepmc_text(src["_pmcid"])
        if t:
            res = dict(text=t, status=200)
            src["via"] = "europepmc"
            attempts.append("europepmc:ok")
    # 2) open-access PDF: OpenAlex, Unpaywall
    oa_urls = []
    if not good(res):
        if (src.get("openalex") or {}).get("pdf"):
            oa_urls.append(("openalex", src["openalex"]["pdf"]))
        if src.get("doi") and cfg.unpaywall_email:
            u = unpaywall_pdf(src["doi"], cfg.unpaywall_email)
            if u:
                oa_urls.append(("unpaywall", u))
    for label, u in oa_urls:
        if good(res) or norm_url(u) == norm_url(url):
            continue
        try:
            r2 = download(u, cfg.fetch_timeout)
            attempts.append(f"{label}:{r2['status']}")
            if good(r2):
                res, src["via"] = r2, f"{label}:{u}"
        except Exception as e:
            attempts.append(f"{label}:{type(e).__name__}")
    # 3) Wayback archive
    if not good(res) and cfg.use_wayback:
        r3 = wayback_fetch(url, doc_date)
        if r3:
            attempts.append(f"wayback:{r3.get('status')}")
            if good(r3):
                res, src["via"] = r3, "wayback:" + r3.get("wayback_ts", "")
                src["wayback_snapshot"] = r3.get("wayback_ts")
                src["meta_dates"] += [d for d in r3.get("dates", []) if d[0] != "trafilatura"]
    # 4) at least the abstract (OpenAlex / Europe PMC) – partial text
    abstract = src.pop("_abstract", "") or ""
    src.pop("_pmcid", None)
    if not good(res) and len(abstract) >= 300:
        res = dict(text=abstract, status=200)
        src["via"] = "santrauka (OpenAlex/Europe PMC)"
        src["partial"] = True
        attempts.append("abstract:ok")

    src["attempts"] = attempts
    text = clean_text(re.sub(r"[ \t]+", " ", (res or {}).get("text") or ""))
    if res and (good(res) or src.get("partial")):
        src["status"] = "ok"
        src["chars"] = len(text)
        src["text"] = text
    else:
        src["status"] = "blocked" if res and res.get("blocked") else "error"
        src["chars"] = 0
    return src


def load_local_sources(cfg: Config) -> list[dict]:
    """User-provided sources (e.g. paywalled articles as PDF) – always compared."""
    folder = Path(cfg.local_sources)
    out = []
    if not folder.exists():
        return out
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() not in (".pdf", ".txt", ".html", ".htm", ".md") or \
                p.stem.upper().startswith(("SKAITYKITE", "README")):
            continue
        try:
            if p.suffix.lower() == ".pdf":
                text, meta = _pdf_text(p.read_bytes())
                title = meta.get("title") or p.stem
            elif p.suffix.lower() in (".html", ".htm"):
                text, meta = _html_text(p.read_text(encoding="utf-8", errors="replace"), "")
                title = meta.get("title") or p.stem
            else:
                text, title = p.read_text(encoding="utf-8", errors="replace"), p.stem
        except Exception as e:
            log(f"Failed to read {p}: {e}")
            continue
        y = re.search(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)", p.stem)
        out.append(dict(sid="local_" + sha1(str(p))[:10], url=p.resolve().as_uri(), nurl=str(p), domain="(lokalus)",
                        title=clean_text(title), status="ok", via="local", text=clean_text(text), chars=len(text),
                        local_year=int(y.group(1)) if y else None, meta_dates=[], search_dates=[], authors=[],
                        local=True))
    return out


def run(cfg: Config):
    seg = read_json(cfg.path("segments.json"))
    cands = read_json(cfg.path("candidates.json"), [])
    doc_date = seg["meta"].get("date") or ""
    sel = [c for c in cands if c.get("selected")]
    src_dir = cfg.path("sources")
    src_dir.mkdir(exist_ok=True)
    cache = KVCache(cfg.path("cache_fetch.sqlite"))
    sources = []
    todo = []
    todo_meta = []      # already downloaded sources that lack the new metadata (dates)
    for c in sel:
        hit = cache.get(c["nurl"])
        # failed attempts from an older version are retried (new fallbacks were added)
        if hit and (hit.get("status") == "ok" or hit.get("fetch_v", 1) >= FETCH_VERSION):
            if hit.get("meta_v", 1) < FETCH_VERSION:
                todo_meta.append(hit)
            sources.append(hit)
        else:
            todo.append(c)
    log(f"Sources selected: {len(sel)}, already downloaded: {len(sources)}, remaining: {len(todo)}")
    prog = Progress(len(todo), "Downloading")
    with ThreadPoolExecutor(max_workers=cfg.fetch_workers) as ex:
        futs = {ex.submit(fetch_one, c, cfg, doc_date): c for c in todo}
        for f in as_completed(futs):
            c = futs[f]
            try:
                s = f.result()
            except Exception as e:
                s = dict(sid=sha1(c["nurl"])[:16], url=c["url"], nurl=c["nurl"], domain=c["domain"],
                         title=c.get("title", ""), status="error", attempts=[repr(e)[:200]], meta_dates=[],
                         search_dates=c.get("dates", []), authors=[])
            text = s.pop("text", "")
            if text:
                (src_dir / f"{s['sid']}.txt").write_text(text, encoding="utf-8")
            cache.set(c["nurl"], s)
            sources.append(s)
            prog.tick(extra=f"{s['status']} {s['domain']}")
    if todo_meta:
        log(f"Additional metadata (dates) for {len(todo_meta)} already downloaded sources...")
        pm = Progress(len(todo_meta), "Metadata")

        def em(s):
            enrich_meta(s, cfg, True)
            s.pop("_abstract", None)
            s.pop("_pmcid", None)
            return s
        with ThreadPoolExecutor(max_workers=cfg.fetch_workers) as ex:
            for f in as_completed([ex.submit(em, s) for s in todo_meta]):
                try:
                    s = f.result()
                    cache.set(s["nurl"], s)
                except Exception as e:
                    log("Metadata error:", repr(e)[:150])
                pm.tick()
    for s in load_local_sources(cfg):
        text = s.pop("text")
        (src_dir / f"{s['sid']}.txt").write_text(text, encoding="utf-8")
        sources.append(s)
    for s in sources:
        resolve_dates(s, doc_date)
    write_json(cfg.path("sources.json"), sources)
    st = {}
    for s in sources:
        st[s["status"]] = st.get(s["status"], 0) + 1
    log(f"Sources: {st} -> {cfg.path('sources.json')}")
    return sources
