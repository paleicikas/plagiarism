"""Stage 2: the LLM assesses each relevant source (its match pairs) and writes an overall conclusion.

If no LLM is available (LLM_PROVIDER=none), heuristic labels are used and the report is still generated.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .config import Config
from .util import KVCache, Progress, log, read_json, sha1, write_json

PAIR_LABELS = {
    "pazodinis_be_nuorodos": "Perimta pažodžiui ar beveik pažodžiui be nuorodos į šaltinį",
    "pazodinis_su_nuoroda_be_kabuciu": "Perimta pažodžiui be kabučių (autorius nurodytas)",
    "perfrazuota_be_nuorodos": "Artimai perfrazuota be nuorodos",
    "perfrazuota_su_nuoroda": "Perfrazuota su nuoroda (priimtina)",
    "tinkamas_citavimas": "Tinkamai cituota (kabutės ir nuoroda)",
    "bendrine_fraze": "Bendrinė frazė, terminas ar klausimyno teiginys",
    "atvirkstinis": "Šaltinis vėlesnis už disertaciją (tikėtina, tekstą perėmė iš jos)",
    "autoriaus_darbas": "Paties autoriaus kitas darbas",
    "klaidingas_sutapimas": "Atsitiktinis sutapimas",
}
PROBLEM_LABELS = {"pazodinis_be_nuorodos", "pazodinis_su_nuoroda_be_kabuciu", "perfrazuota_be_nuorodos"}
SOURCE_VERDICTS = {
    "naudotas_be_nuorodu": "Panaudotas be tinkamų nuorodų (galimas plagiatas)",
    "naudotas_dalinai_be_nuorodu": "Iš dalies panaudotas be tinkamų nuorodų",
    "naudotas_su_nuorodomis": "Panaudotas tinkamai (su nuorodomis)",
    "velesnis_saltinis": "Vėlesnis už disertaciją – negali būti plagiato šaltinis",
    "autoriaus_darbas": "Paties autoriaus darbas",
    "nereiksmingas": "Sutapimai nereikšmingi",
}
CAT_LT = {"identical": "pažodinis", "minor_edit": "nedideli pakeitimai", "partial": "dalinis sutapimas",
          "paraphrase": "galimas perfrazavimas"}

SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "pairs": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"},
            "label": {"type": "string", "enum": list(PAIR_LABELS)},
            "note": {"type": "string", "description": "Short explanation in Lithuanian (up to 25 words)"}},
            "required": ["id", "label"]}},
        "verdict": {"type": "string", "enum": list(SOURCE_VERDICTS)},
        "confidence": {"type": "number", "description": "0..1"},
        "usage_types": {"type": "array", "items": {"type": "string", "enum": [
            "pažodinis kopijavimas", "kopijavimas su nedideliais pakeitimais", "perfrazavimas",
            "struktūros / argumentų sekos perėmimas", "citavimas"]}},
        "summary": {"type": "string", "description": "2–5 sentence conclusion about this source, in Lithuanian"},
    },
    "required": ["pairs", "verdict", "confidence", "summary"],
}

FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_level": {"type": "string", "enum": ["žema", "vidutinė", "aukšta"]},
        "headline": {"type": "string", "description": "One-sentence conclusion, in Lithuanian"},
        "conclusion": {"type": "string", "description": "Detailed conclusion, 2–4 paragraphs, in Lithuanian"},
        "key_findings": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["risk_level", "headline", "conclusion", "key_findings", "limitations"],
}

SYSTEM = """You are an academic integrity expert checking a doctoral dissertation for plagiarism.
You are given automatically detected pairs of matching text between the dissertation and one external source.
Task: assign a label to each pair and assess whether, how and to what extent the source was used.

Important rules:
- If the source date is LATER than the dissertation's, it cannot be a plagiarism source: label such pairs "atvirkstinis",
  unless the text clearly also appears in an older source (then assess by content, but mention the doubt).
- If the source is the dissertation author's own work or a copy of the dissertation itself – "autoriaus_darbas".
- A quotation in quotation marks with a reference = "tinkamas_citavimas". Verbatim text without quotation marks, even if
  the author is cited, is "pazodinis_su_nuoroda_be_kabuciu".
- A common case: the source (e.g. a review) cites other authors, and the dissertation reproduces THIS source's text,
  citing only the primary authors. This is taking over a secondary source's text without referencing it – label it
  "pazodinis_be_nuorodos" or "perfrazuota_be_nuorodos" and explain in the note.
- Standard questionnaire items, scale names, statistical terms, short common phrases – "bendrine_fraze".
- If the match is coincidental (same topic, but different text) – "klaidingas_sutapimas".
- Be strict but fair: do not overrate isolated short matches; however, several consecutive matching phrases from the
  same source are strong evidence.
- If only a search fragment (snippet) is available, assess more cautiously and lower your confidence.
Write your answers in Lithuanian, as an expert reviewer; do not mention artificial intelligence, language models, or that the assessment is automated.
Refer to locations by page numbers (not by internal sentence numbers)."""


# ---------------------------------------------------------------- LLM client

class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache = KVCache(cfg.path("cache_llm.sqlite"))
        self.provider = cfg.llm_provider
        self.usage = {"input": 0, "output": 0}
        if self.provider == "anthropic":
            if not cfg.anthropic_key:
                raise SystemExit("ANTHROPIC_API_KEY is not set (or set LLM_PROVIDER=none)")
            import anthropic
            self.client = anthropic.Anthropic(api_key=cfg.anthropic_key, max_retries=5)
        elif self.provider == "openai":
            if not cfg.openai_key:
                raise SystemExit("OPENAI_API_KEY is not set")

    def json(self, system: str, user: str, schema: dict, model: str, max_tokens: int = 8000) -> dict:
        key = sha1(json.dumps([self.provider, model, system, user, schema], ensure_ascii=False))
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        if self.provider == "anthropic":
            out = None
            sys_full = system + "\nProvide your answer ONLY by calling the `submit` tool."
            for attempt in range(2):
                # newer models do not support forced tool_choice – use "auto" + instruction
                resp = self.client.messages.create(
                    model=model, max_tokens=max_tokens, system=sys_full,
                    messages=[{"role": "user", "content": user}],
                    tools=[{"name": "submit", "description": "Submit the assessment", "input_schema": schema}],
                    tool_choice={"type": "auto"})
                self.usage["input"] += resp.usage.input_tokens
                self.usage["output"] += resp.usage.output_tokens
                out = next((b.input for b in resp.content if b.type == "tool_use"), None)
                if out is None:       # replied with text – try to extract JSON
                    txt = "".join(getattr(b, "text", "") for b in resp.content)
                    m = re.search(r"\{.*\}", txt, re.S)
                    try:
                        out = json.loads(m.group(0)) if m else None
                    except Exception:
                        out = None
                if out is not None:
                    break
                user = user + "\n\nIMPORTANT: answer by calling the `submit` tool."
            if out is None:
                raise RuntimeError("LLM did not return a structured response")
        else:
            base = self.cfg.openai_base_url or "https://api.openai.com/v1"
            r = requests.post(f"{base}/chat/completions", timeout=300,
                              headers={"Authorization": f"Bearer {self.cfg.openai_key}"},
                              json={"model": model, "response_format": {"type": "json_object"},
                                    "messages": [{"role": "system", "content": system + "\nRespond only with JSON matching "
                                                  "the schema: " + json.dumps(schema, ensure_ascii=False)},
                                                 {"role": "user", "content": user}]})
            r.raise_for_status()
            j = r.json()
            u = j.get("usage", {})
            self.usage["input"] += u.get("prompt_tokens", 0)
            self.usage["output"] += u.get("completion_tokens", 0)
            out = json.loads(j["choices"][0]["message"]["content"])
        self.cache.set(key, out)
        return out


# ---------------------------------------------------------------- prompts

def source_prompt(src: dict, pairs: list[dict], sents: list[dict], meta: dict) -> str:
    L = []
    L.append(f"DISSERTATION: „{meta.get('title', '')}“, author {meta.get('author', '')}, date {meta.get('date', '')}.")
    L.append("")
    L.append("SOURCE:")
    L.append(f"- Title: {src.get('title') or '(unknown)'}")
    L.append(f"- URL: {src.get('url')}")
    L.append(f"- Authors: {', '.join(src.get('surnames') or []) or 'NOT determined automatically (judge from the text)'}")
    L.append(f"- Publication date: {src.get('date_note', 'unknown')} → relation to the dissertation: "
             f"{ {'before': 'EARLIER', 'after': 'LATER', 'same_year': 'same year', 'unknown': 'unknown'}.get(src.get('date_status'), 'unknown') }")
    ev = {"fulltext": "full source text", "abstract": "source abstract only (+ search fragments)",
          "snippet": "search fragments (snippets) only"}.get(src.get("evidence"), src.get("evidence"))
    L.append(f"- Evidence type: {ev}")
    if src.get("ref_match"):
        L.append("- THIS SOURCE IS in the dissertation's reference list (found by title): "
                 + " || ".join(src["ref_match"]))
    else:
        L.append("- This source was NOT FOUND by title in the dissertation's reference list "
                 "(automatic search; if the source is a copy or reprint of another work, it may be listed under a different title).")
    if src.get("authors_known"):
        L.append(f"- Source author surnames found in the reference list: {', '.join(src.get('in_refs') or []) or 'none'}; "
                 f"in the dissertation text: {', '.join(src.get('in_text') or []) or 'none'}")
    else:
        L.append("- The source authors were not determined, so it was NOT POSSIBLE to automatically check whether their surnames are in the list – "
                 "do not conclude that the source is missing from the list on this basis alone.")
    if src.get("mentions_author"):
        L.append("- NOTE: the source mentions the dissertation author's surname, but it is NOT the author's own publication "
                 "based on the dissertation (it may be a text about the author, one citing them, or something else). "
                 "Assess by date and content.")
    L.append(f"- Total matching dissertation sentences: {src.get('n_pairs')} ({src.get('words')} words, "
             f"{src.get('pct')} % of the dissertation text); longest continuous span {src.get('longest')} words.")
    L.append("")
    L.append("MATCH PAIRS (D = dissertation, S = source; metrics: cov – share of the dissertation sentence's words in shared "
             "sequences, cont – share of shared content words):")
    for p in pairs:
        s = sents[p["sid"]]
        prev = sents[p["sid"] - 1]["text"] if p["sid"] > 0 else ""
        L.append(f"\n[{p['sid']}] p. {p['page']}, automatic category: {CAT_LT[p['cat']]}, cov={p['cov']}, "
                 f"cont={p['cont']}; sentence contains a reference: {'yes' if p['cite'] else 'no'}; quotation marks: "
                 f"{'yes' if p['quote'] else 'no'}; source author mentioned nearby: {('yes' if p['cited_near'] else 'no') if src.get('authors_known') else 'unknown'}")
        if prev:
            L.append(f"  (previous D sentence: {prev[:300]})")
        L.append(f"  D: {s['text']}")
        L.append(f"  S: {p['src_text'][:900]}")
    L.append("\nAssign a label to EACH pair (id = the number in square brackets) and give the source verdict.")
    return "\n".join(L)


def pick_pairs(pairs: list[dict], n: int) -> list[dict]:
    order = {"identical": 0, "minor_edit": 1, "partial": 2, "paraphrase": 3}
    if len(pairs) <= n:
        return pairs
    top = sorted(pairs, key=lambda p: (order[p["cat"]], -p["cov"]))[:n]
    return sorted(top, key=lambda p: p["sid"])


def heuristic_pair(p: dict, src: dict) -> str:
    if src.get("self"):
        return "autoriaus_darbas"
    if src.get("date_status") == "after":
        return "atvirkstinis"
    if p["quote"] and (p["cite"] or p["cited_near"]):
        return "tinkamas_citavimas"
    if p["cat"] in ("identical", "minor_edit", "partial"):
        return "pazodinis_su_nuoroda_be_kabuciu" if p["cited_near"] else "pazodinis_be_nuorodos"
    return "perfrazuota_su_nuoroda" if p["cited_near"] else "perfrazuota_be_nuorodos"


def heuristic_source(src: dict, labels: dict) -> dict:
    vals = list(labels.values())
    prob = sum(v in PROBLEM_LABELS for v in vals)
    if src.get("self"):
        v = "autoriaus_darbas"
    elif src.get("date_status") == "after":
        v = "velesnis_saltinis"
    elif prob >= 3 or prob >= 0.6 * max(len(vals), 1):
        v = "naudotas_be_nuorodu"
    elif prob:
        v = "naudotas_dalinai_be_nuorodu"
    else:
        v = "naudotas_su_nuorodomis"
    return dict(verdict=v, confidence=0.4, usage_types=[], summary="Automatinis įvertinimas pagal sutapimų metrikas.")


# ---------------------------------------------------------------- run

def run(cfg: Config, only_top: int | None = None):
    seg = read_json(cfg.path("segments.json"))
    mt = read_json(cfg.path("matches.json"))
    sents, meta = seg["sentences"], seg["meta"]
    rel = [s for s in mt["sources"] if s["relevant"]]
    if only_top:
        rel = rel[:only_top]
    use_llm = cfg.llm_provider != "none"
    llm = LLM(cfg) if use_llm else None
    log(f"Sources to assess: {len(rel)} ({'LLM ' + cfg.llm_model if use_llm else 'heuristics'})")
    verdicts = {}
    fatal_errors = []
    prog = Progress(len(rel), "LLM assessment")

    def judge(src):
        pairs = pick_pairs(src["pairs"], cfg.llm_max_pairs)
        if not use_llm:
            labels = {p["sid"]: heuristic_pair(p, src) for p in pairs}
            out = heuristic_source(src, labels)
            out["pairs"] = [dict(id=k, label=v, note="") for k, v in labels.items()]
            out["llm"] = False
            return out
        out = llm.json(SYSTEM, source_prompt(src, pairs, sents, meta), SOURCE_SCHEMA, cfg.llm_model)
        out = dict(out)
        out["llm"] = True
        valid = {p["sid"] for p in pairs}
        clean = []
        for lp in out.get("pairs", []) or []:
            try:
                i = int(str(lp.get("id", "")).strip("[] "))
            except ValueError:
                continue
            if i in valid and lp.get("label") in PAIR_LABELS:
                clean.append(dict(id=i, label=lp["label"], note=lp.get("note", "")))
        out["pairs"] = clean
        if out.get("verdict") not in SOURCE_VERDICTS:
            out["verdict"] = heuristic_source(src, {p["id"]: p["label"] for p in clean})["verdict"]
        got = {p["id"] for p in clean}
        for p in pairs:              # LLM skipped a pair -> heuristic label
            if p["sid"] not in got:
                out.setdefault("pairs", []).append(dict(id=p["sid"], label=heuristic_pair(p, src), note="(automatinė žyma)"))
        return out

    with ThreadPoolExecutor(max_workers=cfg.llm_workers if use_llm else 1) as ex:
        futs = {ex.submit(judge, s): s for s in rel}
        for f in as_completed(futs):
            s = futs[f]
            try:
                verdicts[s["sid"]] = f.result()
            except Exception as e:
                msg = str(getattr(e, "message", "") or e)
                log(f"LLM error ({s['domain']}): {type(e).__name__}: {msg[:500]}")
                fatal_errors.append(msg)
                if len(fatal_errors) >= 3 and len(set(m[:80] for m in fatal_errors[-3:])) == 1 and \
                        not any(v.get("llm") for v in verdicts.values()):
                    ex.shutdown(cancel_futures=True)
                    raise SystemExit("LLM requests keep failing (same error) – stopping. "
                                     "Check the API key, model name (LLM_MODEL) and credit balance.")
                pairs = pick_pairs(s["pairs"], cfg.llm_max_pairs)
                labels = {p["sid"]: heuristic_pair(p, s) for p in pairs}
                v = heuristic_source(s, labels)
                v["pairs"] = [dict(id=k, label=l, note="") for k, l in labels.items()]
                v["llm"] = False
                v["error"] = repr(e)[:300]
                verdicts[s["sid"]] = v
            prog.tick()

    # pairs not sent to LLM (beyond llm_max_pairs) inherit heuristic labels
    for s in rel:
        v = verdicts[s["sid"]]
        have = {int(p["id"]) for p in v["pairs"]}
        for p in s["pairs"]:
            if p["sid"] not in have:
                v["pairs"].append(dict(id=p["sid"], label=heuristic_pair(p, s), note="(automatinė žyma)"))

    stats = summarize(rel, verdicts, sents, mt["stats"])
    final = None
    if use_llm:
        try:
            final = llm.json(SYSTEM, final_prompt(rel, verdicts, stats, meta), FINAL_SCHEMA,
                             cfg.llm_final_model or cfg.llm_model, max_tokens=6000)
        except Exception as e:
            log(f"Final conclusion error: {e!r}"[:300])
    if not final:
        final = heuristic_final(stats)
    out = dict(verdicts=verdicts, stats=stats, final=final, model=cfg.llm_model if use_llm else None,
               usage=llm.usage if llm else None)
    write_json(cfg.path("verdicts.json"), out)
    if llm:
        log(f"LLM tokens: input {llm.usage['input']}, output {llm.usage['output']}")
    log(f"Conclusion: risk {final['risk_level']} – {final['headline']} -> {cfg.path('verdicts.json')}")
    return out


def summarize(rel, verdicts, sents, base_stats):
    """For each sentence – the "worst" label across all sources; the extent is computed from that."""
    severity = ["pazodinis_be_nuorodos", "perfrazuota_be_nuorodos", "pazodinis_su_nuoroda_be_kabuciu",
                "perfrazuota_su_nuoroda", "tinkamas_citavimas", "autoriaus_darbas", "atvirkstinis",
                "bendrine_fraze", "klaidingas_sutapimas"]
    rank = {l: i for i, l in enumerate(severity)}
    best = {}
    for s in rel:
        for p in verdicts[s["sid"]]["pairs"]:
            sid, lab = int(p["id"]), p["label"]
            if lab not in rank:
                continue
            if sid not in best or rank[lab] < rank[best[sid][0]]:
                best[sid] = (lab, s["sid"])
    by_label = {}
    for sid, (lab, _) in best.items():
        d = by_label.setdefault(lab, {"sentences": 0, "words": 0})
        d["sentences"] += 1
        d["words"] += sents[sid]["words"]
    tw = base_stats["total_words"]
    for d in by_label.values():
        d["pct"] = round(100 * d["words"] / max(tw, 1), 2)
    prob_words = sum(by_label.get(l, {}).get("words", 0) for l in PROBLEM_LABELS)
    by_verdict = {}
    for s in rel:
        v = verdicts[s["sid"]]["verdict"]
        by_verdict[v] = by_verdict.get(v, 0) + 1
    pages = sorted({sents[sid]["page"] for sid, (lab, _) in best.items() if lab in PROBLEM_LABELS})
    return dict(base_stats, sentence_labels={str(k): v[0] for k, v in best.items()},
                sentence_source={str(k): v[1] for k, v in best.items()}, by_label=by_label,
                problem_words=prob_words, problem_pct=round(100 * prob_words / max(tw, 1), 2),
                problem_pages=pages, by_verdict=by_verdict)


def final_prompt(rel, verdicts, stats, meta) -> str:
    L = [f"DISSERTATION: „{meta.get('title', '')}“, {meta.get('author', '')}, {meta.get('date', '')}. "
         f"Checked {stats['sentences']} sentences, {stats['total_words']} words.",
         "", "SUMMARY BY LABEL (sentences / words / % of the dissertation text):"]
    for lab, d in sorted(stats["by_label"].items(), key=lambda x: -x[1]["words"]):
        L.append(f"- {PAIR_LABELS.get(lab, lab)}: {d['sentences']} / {d['words']} / {d['pct']} %")
    L.append(f"Total problematic words (without proper references): {stats['problem_words']} ({stats['problem_pct']} %), "
             f"on pages: {', '.join(map(str, stats['problem_pages'][:80]))}")
    L.append("\nSOURCES (verdict, confidence, extent, date, summary):")
    for s in rel:
        v = verdicts[s["sid"]]
        prob = sum(p["label"] in PROBLEM_LABELS for p in v["pairs"])
        L.append(f"- {s.get('title') or s['domain']} | {s['url']} | {v['verdict']} ({v.get('confidence')}) | "
                 f"{s['n_pairs']} sent., {s['pct']} %, problematic pairs {prob} | date: {s.get('date_note')} | "
                 f"{v.get('summary', '')[:600]}")
    L.append("\nWrite an overall conclusion in Lithuanian: whether the dissertation likely uses external sources without proper references, "
             "which sources, in what way (verbatim, with modifications, by paraphrasing) and to what extent (percentages, "
             "pages). Clearly separate proven cases from doubtful ones. State the limitations of the method. "
             "Refer to locations by page numbers – do NOT cite internal sentence numbers (IDs).")
    return "\n".join(L)


def heuristic_final(stats) -> dict:
    p = stats["problem_pct"]
    risk = "aukšta" if p >= 5 else "vidutinė" if p >= 1 else "žema"
    return dict(risk_level=risk,
                headline=f"Be tinkamų nuorodų sutampa apie {p} % disertacijos teksto (preliminarus automatinis įvertinimas).",
                conclusion="Ši išvada remiasi tik automatinėmis sutapimų metrikomis. "
                           "Rekomenduojama sutapimus peržiūrėti rankiniu būdu.",
                key_findings=[], limitations=["Automatinis vertinimas neatskiria bendrinių frazių ir citavimo niuansų."])
