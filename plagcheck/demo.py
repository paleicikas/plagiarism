"""Synthetic demo: a small thesis PDF plus local "sources" that it partly copies.

Runs completely offline (no search engine, no LLM), so it is used by the self-test of the packaged app,
by the tests and to build the sample report on the website:

    python -m plagcheck.demo --out site/demo/report.html --lang en
"""
from __future__ import annotations

import argparse
import html
import shutil
import tempfile
from pathlib import Path

from .config import Config

TITLE = "PHYSICAL ACTIVITY AND WELLBEING AMONG UNIVERSITY STUDENTS"
AUTHOR = "Laura Mitchell"

# Source published before the thesis – parts of it are copied (verbatim, with edits, paraphrased, quoted)
SOURCE_REVIEW = """Physical activity and mental health in young adults: a review. Smith, J. and Clark, R. (2008).
Regular physical activity is associated with lower levels of anxiety and depressive symptoms in young adults across a wide range of cultural settings.
The protective effect appears to be strongest when exercise is performed at moderate intensity for at least thirty minutes on most days of the week.
University students represent a particularly vulnerable group because the transition to higher education often disrupts established routines of sleep, diet and exercise.
Several longitudinal studies have shown that students who remain physically active during their first academic year report higher life satisfaction than their inactive peers.
The mechanisms linking exercise and wellbeing are not fully understood, but they are likely to involve both physiological and psychological pathways.
Physiological explanations emphasise the release of endorphins, improved sleep quality and the regulation of the stress hormone cortisol.
Psychological explanations focus on the sense of mastery, social contact and distraction from worries that structured physical activity provides.
Interventions that combine information about the benefits of exercise with practical support, such as free access to sports facilities, tend to be more effective than information campaigns alone.
Barriers most frequently mentioned by students include lack of time, academic pressure, cost of membership and a lack of companions to exercise with.
Future research should examine how digital tools, such as activity trackers and smartphone applications, can be used to sustain motivation over longer periods.
"""

# Older article that the thesis cites and paraphrases properly
SOURCE_ARTICLE = """Self-determination and exercise adherence. Brown, K. (2012). Journal of Health Behaviour.
Motivation that is autonomous, meaning that the person exercises because they value or enjoy the activity, predicts long-term adherence much better than motivation driven by external pressure or guilt.
Programmes that offer choice and emphasise enjoyment therefore tend to retain participants for longer than programmes based on strict prescriptions.
Feedback that highlights personal progress strengthens the feeling of competence and makes it more likely that people continue exercising.
"""

# Blog post published AFTER the thesis that copied text from it (reverse direction)
SOURCE_LATER = """Why students should move more. Campus Wellness Blog (2021).
In this study a questionnaire survey of 412 undergraduate students from three faculties was carried out in the spring semester.
Students who met the recommended level of weekly activity reported significantly better sleep and lower perceived stress than students who did not.
The results suggest that universities should treat physical activity as an integral part of student support services rather than as a leisure option.
"""

CHAPTERS = [
    ("1. INTRODUCTION", [
        "Student wellbeing has become an increasingly important topic for universities, policy makers and health professionals.",
        "Regular physical activity is associated with lower levels of anxiety and depressive symptoms in young adults across a wide range of cultural settings. "
        "The protective effect appears to be strongest when exercise is performed at moderate intensity for at least thirty minutes on most days of the week.",
        "University students are a particularly vulnerable group because the move to higher education often disrupts established routines of sleep, diet and physical exercise. "
        "Several longitudinal studies have shown that students who stay physically active during their first academic year report higher life satisfaction than their inactive peers.",
        "The aim of this thesis is to examine how physical activity relates to wellbeing among university students and which factors help students stay active.",
    ]),
    ("2. THEORETICAL BACKGROUND", [
        "The mechanisms linking exercise and wellbeing are not fully understood, but they are likely to involve both physiological and psychological pathways. "
        "Physiological explanations emphasise the release of endorphins, improved sleep quality and the regulation of the stress hormone cortisol.",
        "Psychological accounts, on the other hand, stress that organised exercise gives people a feeling of mastery, opportunities for social contact and a break from everyday worries.",
        "According to Brown (2012), people who exercise because they genuinely value or enjoy the activity keep exercising far longer than those who are pushed by outside pressure or guilt. "
        "For this reason, programmes offering choice and enjoyment usually keep their participants longer (Brown, 2012).",
        "As Smith and Clark (2008) put it, \"interventions that combine information about the benefits of exercise with practical support, such as free access to sports facilities, tend to be more effective than information campaigns alone\" (p. 14).",
        "Barriers most frequently mentioned by students include lack of time, academic pressure, cost of membership and a lack of companions to exercise with.",
    ]),
    ("3. METHODOLOGY", [
        "In this study a questionnaire survey of 412 undergraduate students from three faculties was carried out in the spring semester.",
        "Physical activity was measured with a short self-report instrument covering walking, moderate and vigorous activity during the previous seven days.",
        "Wellbeing was assessed with a validated life satisfaction scale and a short measure of perceived stress.",
        "Participation was voluntary and anonymous, and all students gave informed consent before completing the questionnaire.",
    ]),
    ("4. RESULTS AND DISCUSSION", [
        "Students who met the recommended level of weekly activity reported significantly better sleep and lower perceived stress than students who did not.",
        "The association remained significant after controlling for gender, year of study and part-time employment.",
        "The results suggest that universities should treat physical activity as an integral part of student support services rather than as a leisure option.",
        "Future research should examine how digital tools, such as activity trackers and smartphone applications, can be used to sustain motivation over longer periods.",
    ]),
    ("5. CONCLUSIONS", [
        "Physical activity is clearly related to the wellbeing of university students in the sample studied.",
        "Support measures that reduce practical barriers and make exercise enjoyable are likely to be the most effective way to help students stay active.",
    ]),
]

REFERENCES = [
    "Brown, K. (2012). Self-determination and exercise adherence. Journal of Health Behaviour, 18(2), 101-115.",
    "Smith, J., & Clark, R. (2008). Physical activity and mental health in young adults: a review. Health Review, 4, 9-30.",
    "World Health Organization (2010). Global recommendations on physical activity for health. Geneva: WHO.",
]

CSS = "body{font-family:serif;font-size:11pt;line-height:1.45} p{text-indent:22pt;margin:0 0 6pt 0;text-align:left} " \
      "h1{font-size:13pt;margin:0 0 10pt 0} .c{text-align:center;text-indent:0}"


def _page(doc, body_html: str):
    import pymupdf
    page = doc.new_page(width=595, height=842)
    page.insert_htmlbox(pymupdf.Rect(72, 72, 523, 770), body_html, css=CSS)
    return page


def make_pdf(path: Path):
    import pymupdf
    doc = pymupdf.open()
    _page(doc, f"<p class='c'>UNIVERSITY OF EXAMPLE</p><p class='c'>&nbsp;</p><p class='c'>&nbsp;</p>"
               f"<p class='c'><b>{TITLE}</b></p><p class='c'>&nbsp;</p><p class='c'>{AUTHOR}</p>"
               f"<p class='c'>&nbsp;</p><p class='c'>Master's thesis</p><p class='c'>2015</p>")
    _page(doc, "<h1>ABSTRACT</h1><p>This thesis examines the relationship between physical activity and the "
               "wellbeing of university students using a questionnaire survey.</p>")
    toc = "".join(f"<p class='c'>{html.escape(h)} . . . . . . . . . . . . . . . {4 + i}</p>"
                  for i, (h, _) in enumerate(CHAPTERS))
    _page(doc, "<h1>CONTENTS</h1>" + toc)
    for head, paras in CHAPTERS:
        _page(doc, f"<h1>{html.escape(head)}</h1>" + "".join(f"<p>{html.escape(p)}</p>" for p in paras))
    _page(doc, "<h1>REFERENCES</h1>" + "".join(f"<p style='text-indent:0'>{html.escape(r)}</p>" for r in REFERENCES))
    doc.set_metadata({"title": TITLE.title(), "author": AUTHOR, "creationDate": "D:20150601120000"})
    doc.save(str(path))


META = dict(date="2015-06-01", author=AUTHOR)


def write_sources(folder: Path):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Smith_Clark_2008_review.txt").write_text(SOURCE_REVIEW, encoding="utf-8")
    (folder / "Brown_2012_article.txt").write_text(SOURCE_ARTICLE, encoding="utf-8")
    (folder / "Campus_blog_2021.txt").write_text(SOURCE_LATER, encoding="utf-8")


def build(folder: Path, lang: str = "en") -> Config:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / "demo-thesis.pdf"
    make_pdf(pdf)
    src = folder / "local_sources"
    write_sources(src)
    return Config.blank(pdf=str(pdf), work=str(folder / "work"), local_sources=str(src), search_backend="none",
                        llm_provider="none", use_wayback=False, report_lang=lang, doc_date=META["date"],
                        doc_author=AUTHOR)


def run(folder: Path | None = None, lang: str = "en", out: str | None = None, on_event=None) -> Path:
    from .pipeline import run_pipeline
    folder = Path(folder or tempfile.mkdtemp(prefix="plagcheck-demo-"))
    cfg = build(folder, lang)
    run_pipeline(cfg, ["extract", "search", "fetch", "align", "verify", "report"], on_event=on_event, yes=True,
                 out=out, report_lang=lang)
    return Path(out) if out else cfg.path("index.html")


def main():
    ap = argparse.ArgumentParser(description="Build the offline demo report")
    ap.add_argument("--out", help="Where to write the report HTML")
    ap.add_argument("--dir", help="Working folder (default: a temporary folder)")
    ap.add_argument("--lang", default="en")
    a = ap.parse_args()
    if not a.out:
        print(run(a.dir, a.lang))
        return
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    folder = a.dir or tempfile.mkdtemp(prefix="plagcheck-demo-")
    try:
        print(run(folder, a.lang, a.out))
    finally:
        if not a.dir:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
