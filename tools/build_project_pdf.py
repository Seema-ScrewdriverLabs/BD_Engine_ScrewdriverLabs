#!/usr/bin/env python
"""
Build PeopleIntel-Project-Guide.pdf — the whole project, on paper.

Content lives in `blocks()` as a flat list, so adding a section is adding a few
calls rather than touching layout code. The numbers in "Where the data stands"
are read from data/peopleintel.db at build time instead of being typed in, for
the same reason the app computes its own stat tiles: a number written by hand is
wrong the next time anything changes.

    python tools/build_project_pdf.py

Needs reportlab, and nothing else.
"""
import os
import sqlite3
import subprocess
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageBreak,
    CondPageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "PeopleIntel-Project-Guide.pdf")
DB = os.path.join(ROOT, "data", "peopleintel.db")

# The app's own palette, so the document and the dashboard look related.
PLUM = colors.HexColor("#9D3266")
PLUM_DARK = colors.HexColor("#6B1D42")
PLUM_PALE = colors.HexColor("#FBF3F7")
VIOLET = colors.HexColor("#7A2FB8")
VIOLET_PALE = colors.HexColor("#F6F1FC")
INK = colors.HexColor("#16181D")
SOFT = colors.HexColor("#4B5563")
FAINT = colors.HexColor("#8A929E")
RULE_C = colors.HexColor("#E2E3E8")
SHADE = colors.HexColor("#F6F6F8")
AMBER = colors.HexColor("#B45309")
AMBER_PALE = colors.HexColor("#FEF6E7")

BODY = "Helvetica"
BOLD = "Helvetica-Bold"
MONO = "Courier"

PAGE_W, PAGE_H = A4
MARGIN = 19 * mm
COL_W = PAGE_W - 2 * MARGIN


# ------------------------------------------------------------------ styles
def _s(name, **kw):
    kw.setdefault("fontName", BODY)
    kw.setdefault("fontSize", 9.3)
    kw.setdefault("leading", 13.8)
    kw.setdefault("textColor", INK)
    return ParagraphStyle(name, **kw)


ST = {
    "title": _s("title", fontName=BOLD, fontSize=36, leading=39, spaceAfter=8),
    "subtitle": _s("subtitle", fontSize=13, leading=19, textColor=SOFT),
    "kicker": _s("kicker", fontName=BOLD, fontSize=8.4, leading=12,
                 textColor=PLUM, spaceAfter=3),
    "h1": _s("h1", fontName=BOLD, fontSize=17, leading=21, spaceAfter=6),
    "h2": _s("h2", fontName=BOLD, fontSize=11.4, leading=15,
             textColor=PLUM_DARK, spaceBefore=11, spaceAfter=4),
    "h3": _s("h3", fontName=BOLD, fontSize=9.7, leading=13, spaceBefore=8,
             spaceAfter=3),
    "body": _s("body", spaceAfter=6),
    "lead": _s("lead", fontSize=10.4, leading=16, textColor=SOFT, spaceAfter=8),
    "bullet": _s("bullet", leftIndent=12, bulletIndent=1, spaceAfter=3.5),
    "note": _s("note", fontSize=8.5, leading=12.4, textColor=SOFT, spaceAfter=5),
    "cell": _s("cell", fontSize=8.4, leading=12),
    "cellh": _s("cellh", fontName=BOLD, fontSize=8, leading=11,
                textColor=colors.white),
    "code": _s("code", fontName=MONO, fontSize=8, leading=12.2,
               textColor=PLUM_DARK, leftIndent=8, spaceAfter=0),
    "cover_meta": _s("cover_meta", fontSize=9, leading=15, textColor=FAINT),
    # A tile's number needs leading of its own. Rendered in the 8.4pt "cell"
    # style its 18pt glyphs overflowed the line box and the label underneath
    # was drawn through them.
    "tile_num": _s("tile_num", fontName=BOLD, fontSize=18, leading=21,
                   textColor=PLUM),
    "tile_lbl": _s("tile_lbl", fontSize=7.4, leading=10, textColor=SOFT),
    "pillar_kicker": _s("pillar_kicker", fontName=BOLD, fontSize=7.4,
                        leading=10, textColor=FAINT),
    "pillar_name": _s("pillar_name", fontName=BOLD, fontSize=12.5, leading=15),
    "pillar_body": _s("pillar_body", fontSize=8.4, leading=12.2,
                      textColor=SOFT),
}


# ------------------------------------------------------------- block helpers
def P(text, style="body"):
    return Paragraph(text, ST[style])


def Rule(color=RULE_C, width=0.6):
    t = Table([[""]], colWidths=[COL_W], rowHeights=[0.1])
    t.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), width, color),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def H1(text, kicker=None):
    out = [Spacer(1, 15), CondPageBreak(58 * mm)]
    if kicker:
        out.append(P(kicker.upper(), "kicker"))
    out.append(P(text, "h1"))
    out.append(Rule(PLUM, 1.1))
    out.append(Spacer(1, 8))
    return out


def Bullets(items):
    return [Paragraph(i, ST["bullet"], bulletText="•") for i in items]


def Numbered(items):
    return [Paragraph(t, ST["bullet"], bulletText="%d." % (n + 1))
            for n, t in enumerate(items)]


def Grid(headers, rows, widths, header_color=PLUM_DARK):
    """A table whose cells are Paragraphs, so long text wraps."""
    data = [[Paragraph(h, ST["cellh"]) for h in headers]]
    for r in rows:
        data.append([c if hasattr(c, "wrap") else Paragraph(str(c), ST["cell"])
                     for c in r])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), header_color),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE_C),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE_C),
    ]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), SHADE))
    t.setStyle(TableStyle(style))
    # Only a short table is worth keeping whole. Holding a seven-row table
    # together pushed it onto a fresh page and left the previous one a
    # quarter full; a long table repeats its header row instead, which is
    # what repeatRows is for.
    if len(data) <= 4:
        return [KeepTogether([t, Spacer(1, 8)])]
    return [t, Spacer(1, 8)]


def Callout(title, body, accent=PLUM, bg=PLUM_PALE):
    inner = [P("<b>%s</b>" % title, "cell"), Spacer(1, 3)]
    paras = body if isinstance(body, (list, tuple)) else [body]
    for i, para in enumerate(paras):
        inner.append(Paragraph(para, ST["cell"]))
        if i < len(paras) - 1:
            inner.append(Spacer(1, 4))
    t = Table([[inner]], colWidths=[COL_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 2.4, accent),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return KeepTogether([Spacer(1, 2), t, Spacer(1, 9)])


def Tiles(pairs, per_row=4):
    """Stat tiles: (value, label)."""
    out = []
    w = COL_W / per_row
    for i in range(0, len(pairs), per_row):
        chunk = list(pairs[i:i + per_row])
        cells = []
        for value, label in chunk:
            cells.append([
                Paragraph(str(value), ST["tile_num"]),
                Spacer(1, 2),
                Paragraph(label.upper(), ST["tile_lbl"]),
            ])
        while len(cells) < per_row:
            cells.append([])
        t = Table([cells], colWidths=[w] * per_row)
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.5, RULE_C),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, RULE_C),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ]))
        out.append(t)
        out.append(Spacer(1, 6))
    return out


def Code(lines):
    text = "<br/>".join(l.replace("&", "&amp;").replace("<", "&lt;")
                        for l in lines)
    t = Table([[Paragraph(text, ST["code"])]], colWidths=[COL_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SHADE),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE_C),
    ]))
    return KeepTogether([t, Spacer(1, 8)])


def Pillars(items):
    """Three side-by-side cards for Company / Designation / Person."""
    w = COL_W / len(items)
    cells = []
    for n, (name, text) in enumerate(items):
        cells.append([
            Paragraph("PILLAR %d" % (n + 1), ST["pillar_kicker"]),
            Spacer(1, 4),
            Paragraph(name, ST["pillar_name"]),
            Spacer(1, 4),
            Paragraph(text, ST["pillar_body"]),
        ])
    t = Table([cells], colWidths=[w] * len(items))
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), PLUM_PALE),
        ("LINEABOVE", (0, 0), (-1, 0), 2.4, PLUM),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
    ]))
    return KeepTogether([t, Spacer(1, 10)])


# --------------------------------------------------------------- live facts
def db_facts():
    """The counts quoted in the snapshot section, read rather than typed."""
    f = {"available": False}
    if not os.path.exists(DB):
        return f
    con = sqlite3.connect(DB)
    q = lambda sql: con.execute(sql).fetchone()[0]
    try:
        f.update(
            available=True,
            people=q("select count(*) from people"),
            companies=q("select count(*) from companies"),
            activities=q("select count(*) from linkedin_activities"),
            findings=q("select count(*) from web_findings"),
            interests=q("select count(*) from interests"),
            steps=q("select count(*) from outreach_steps"),
            complete=q("select count(*) from people "
                       "where enrichment_status = 'complete'"),
            researched=q("select count(*) from people "
                         "where research_status = 'researched'"),
            focus=q("select count(*) from people "
                    "where focus_line is not null and focus_line != ''"),
            drift=q("select count(*) from people "
                    "where title_observed is not null"),
            li_drift=q("select count(*) from people "
                       "where linkedin_observed is not null"),
            drafts=q("select count(*) from linkedin_activities "
                     "where suggested_comment is not null"),
            corroborated=q("select count(*) from web_findings "
                           "where corroborated = 1"),
            searched=q("select count(*) from linkedin_activities "
                       "where added_by = 'search'"),
            pasted=q("select count(*) from linkedin_activities "
                     "where added_by = 'pasted'"),
            with_findings=q("select count(distinct person_id) from web_findings"),
            with_activity=q("select count(distinct person_id) "
                            "from linkedin_activities"),
            # The cross-tab, straight from the database. Deriving these three
            # from the totals is arithmetic that goes wrong quietly — the
            # first version of this section reported one of them off by one.
            complete_researched=q(
                "select count(*) from people where enrichment_status = "
                "'complete' and research_status = 'researched'"),
            complete_pending=q(
                "select count(*) from people where enrichment_status = "
                "'complete' and research_status != 'researched'"),
            needs_researched=q(
                "select count(*) from people where enrichment_status != "
                "'complete' and research_status = 'researched'"),
            countries=con.execute(
                "select country, count(*) from companies "
                "where country is not null and country != '' "
                "group by 1 order by 2 desc").fetchall(),
            act_types=con.execute(
                "select activity_type, count(*) from linkedin_activities "
                "group by 1 order by 2 desc").fetchall(),
        )
    finally:
        con.close()
    return f


def git_log():
    try:
        out = subprocess.run(
            ["git", "log", "--pretty=format:%h|%ad|%s", "--date=short"],
            cwd=ROOT, capture_output=True, text=True, timeout=20)
        rows = [l.split("|", 2) for l in out.stdout.splitlines() if "|" in l]
        return rows
    except Exception:
        return []


def line_counts():
    counts = {}
    for folder in ("app", "templates", "."):
        d = os.path.join(ROOT, folder)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            if not name.endswith((".py", ".html")):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    n = sum(1 for _ in fh)
            except OSError:
                continue
            key = name if folder == "." else "%s/%s" % (folder, name)
            counts[key] = n
    return counts


# ------------------------------------------------------------------ content
def blocks(facts, commits, loc):
    b = []
    add = b.extend
    one = b.append

    total_py = sum(v for k, v in loc.items()
                   if k.endswith(".py") and "build_project_pdf" not in k)
    total_html = sum(v for k, v in loc.items() if k.endswith(".html"))

    # ---------------------------------------------------------------- cover
    one(Spacer(1, 42 * mm))
    one(P("PROJECT GUIDE · BUILD REPORT", "kicker"))
    one(P("PeopleIntel", "title"))
    one(Rule(PLUM, 2.4))
    one(Spacer(1, 10))
    one(P("A lead-research dashboard. A CSV of contacts goes in; a browsable "
          "set of researched profiles comes out — who the person is, what "
          "they do, what they are currently interested in, and what their "
          "company does.", "subtitle"))
    one(Spacer(1, 14))
    add(Tiles([
        (str(facts.get("people", "—")), "contacts"),
        (str(facts.get("companies", "—")), "companies"),
        (str(total_py), "lines of Python"),
        (str(len(commits)), "commits"),
    ]))
    one(Spacer(1, 8))
    one(Pillars([
        ("Company", "What the employer does, researched from their own site "
                    "in preference to anyone else's."),
        ("Designation", "The title on file and the title on the web, kept "
                        "separately — the file's copy goes stale."),
        ("Person", "Who they are, what they have published, and what they are "
                   "actually talking about."),
    ]))
    one(Spacer(1, 6))
    one(P("Python only. Node is not used at runtime and is not needed to run "
          "this. FastAPI, Jinja2, SQLite via SQLAlchemy, a web-search API for "
          "research and any OpenAI-compatible endpoint for the one inference "
          "step.", "note"))
    one(Spacer(1, 10))
    one(Rule())
    one(Spacer(1, 8))
    one(P("Generated %s from the working tree on branch <b>%s</b>. "
          "Counts in this document are read from the code and the database at "
          "build time — rebuild with "
          "<font face=\"Courier\">python tools/build_project_pdf.py</font>."
          % (date.today().strftime("%d %B %Y"), current_branch()),
          "cover_meta"))

    one(NextPageTemplate("body"))
    one(PageBreak())

    # ------------------------------------------------------------------ TOC
    add(H1("What is in this document", "Contents"))
    toc = [
        ("1", "The problem, and the shape of the answer",
         "Why the tool exists and what it refuses to do"),
        ("2", "How it runs", "Install, the two API keys, the CLI"),
        ("3", "The nine steps", "Upload through to reports, and the file each lives in"),
        ("4", "Architecture", "Modules, routes, pages, and what depends on what"),
        ("5", "The data model", "Six tables, and why provenance is a column"),
        ("6", "Two statuses, not one", "The bug that produced the rule"),
        ("7", "What the CSV importer tolerates", "Delimiters, encodings, a broken header row"),
        ("8", "LinkedIn, without logging in", "Three ways a post is proven to be theirs"),
        ("9", "Web research and corroboration", "Namesakes, and the three-signal test"),
        ("10", "Interests, focus lines and comment drafts", "The only LLM calls, and their guardrails"),
        ("11", "Opportunities", "Matching capability to evidence, with the field cited"),
        ("12", "Segmenting the list", "Country and category, derived on read"),
        ("13", "The outreach sequence", "Four manual steps, one open at a time"),
        ("14", "Where the data stands", "The live numbers in this build"),
        ("15", "Build history", "What shipped, and in what order"),
        ("16", "Limits and what is not built", "Stated plainly, on purpose"),
    ]
    rows = []
    for num, title, sub in toc:
        rows.append([
            Paragraph('<font color="#9D3266"><b>%s</b></font>' % num, ST["cell"]),
            Paragraph("<b>%s</b><br/><font color=\"#4B5563\" size=\"8\">%s</font>"
                      % (title, sub), ST["cell"]),
        ])
    t = Table(rows, colWidths=[12 * mm, COL_W - 12 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE_C),
    ]))
    one(t)

    one(Callout(
        "One rule runs through all of it",
        ["Every fact the app fetched itself stores where it came from and when. "
         "Anything that cannot be tied to the contact is not stored — not "
         "stored behind a badge, not stored greyed out. An empty panel that "
         "says “Nothing found” is a correct answer; four plausible "
         "rows about four different people is not.",
         "That single choice is why several sections below read as lists of "
         "things deliberately given up."]))


    # ------------------------------------------------------- 1. the problem
    add(H1("The problem, and the shape of the answer", "Section 1"))
    add([P("A prospecting export gives you rows. It does not tell you who any "
           "of these people are. A 75-column Apollo CSV carries a name, a "
           "title that may be two years out of date, an employer, and little "
           "else you would want to open a conversation with.", "lead")])
    add([P("PeopleIntel takes that file and produces, per contact, a page a "
           "person can read before making contact: a composed profile "
           "paragraph, what their employer does, what they have published, "
           "what they appear to be focused on right now, what can be offered "
           "to them, and a four-step outreach sequence tracking where the "
           "conversation has got to.")])

    add([P("Three pillars", "h2")])
    add([P("Company, Designation, Person. Every panel on the dashboard answers "
           "one of those three, and the data model is arranged around them: "
           "<font face=\"Courier\">Company</font> holds the employer and its "
           "researched description, <font face=\"Courier\">Person</font> holds "
           "identity and both copies of the designation, and the four child "
           "tables hold what was found about them.")])

    add([P("What it deliberately does not do", "h2")])
    add(Bullets([
        "<b>It never logs into LinkedIn.</b> No credentials, no scraper, no "
        "third-party LinkedIn data provider, and it never fetches a "
        "linkedin.com page. Public post URLs reach the panel through the same "
        "web-search index used for everything else, or by hand.",
        "<b>It never guesses an email address.</b> No first.last@domain "
        "pattern construction. A pattern produces a plausible string, not a "
        "fact, and a plausible string in an email field is worse than an empty "
        "one because nothing downstream can tell them apart.",
        "<b>It never infers an interest from a job title.</b> Thin evidence "
        "returns an empty panel. Every interest chip carries the evidence that "
        "produced it, and a chip with an empty evidence field is dropped "
        "before it is displayed.",
        "<b>It never sends anything.</b> No comment is posted, no connection "
        "request is sent, no email goes out. The outreach sequence tracks what "
        "a person did, and drafts a comment for them to edit — automated "
        "LinkedIn engagement is exactly the thing a hand-written first touch "
        "is trying not to look like.",
        "<b>It never silently overwrites the file.</b> Where research "
        "disagrees with the CSV — a job title, a LinkedIn profile — "
        "both values are kept and the mismatch is flagged.",
    ]))


    # ---------------------------------------------------------- 2. how it runs
    add(H1("How it runs", "Section 2"))
    add([P("Install and start", "h2")])
    one(Code([
        "pip install -r requirements.txt",
        "",
        "python -c \"from app.db import init_db; init_db()\"",
        "uvicorn app.main:app --reload --port 8000",
    ]))
    add([P("Open <font face=\"Courier\">http://127.0.0.1:8000</font>, upload a "
           "CSV, and the contacts appear. Six runtime dependencies, all "
           "Python: FastAPI, uvicorn, Jinja2, python-multipart, SQLAlchemy, "
           "httpx. <font face=\"Courier\">static/app.css</font> is committed "
           "prebuilt, so there is no build step and no Node — Tailwind is "
           "only needed to change styles, and its standalone binary needs no "
           "Node either.")])

    add([P("Loading the sample data", "h2")])
    one(Code([
        "python -c \"",
        "from app.db import init_db, SessionLocal",
        "from app.importer import import_csv",
        "init_db(); db = SessionLocal()",
        "print(import_csv(db, open('data/apollo_contacts.csv','rb').read()))\"",
        "python seed_research.py",
    ]))

    add([P("The two API keys", "h2")])
    add(Grid(
        ["Variable", "What it powers", "Without it"],
        [["FIRECRAWL_API_KEY",
          "Web research: company descriptions, the Google/Web panel, the "
          "LinkedIn activity link search, and every enrichment lookup in "
          "resolve.py.",
          "run_pipeline.py exits immediately. The app still runs and still "
          "shows everything the CSV gave it."],
         ["LLM_BASE_URL<br/>LLM_MODEL<br/>LLM_API_KEY",
          "Interest chips, the focus line, and the recommended comment drafts. "
          "Any OpenAI-compatible chat-completions endpoint — Groq now, a "
          "self-hosted Ollama or vLLM by changing the base URL and nothing "
          "else.",
          "Research still runs; the interest and draft steps are skipped. "
          "--skip-interests does this on purpose."]],
        [34 * mm, COL_W - 34 * mm - 42 * mm, 42 * mm]))
    add([P("Read at startup from <font face=\"Courier\">.env</font> by "
           "<font face=\"Courier\">app/env.py</font>; a variable already set in "
           "the shell wins over the file. The server does not watch the file, "
           "so a change needs a restart — and "
           "<font face=\"Courier\">/settings</font> shows which keys are "
           "present without ever printing their values.", "note")])

    add([P("The pipeline CLI", "h2")])
    add(Grid(
        ["Command", "What it does"],
        [["python run_pipeline.py --limit 5",
          "Research five outstanding contacts. The sensible first run — "
          "it costs credits, so try a few before all of them."],
         ["python run_pipeline.py",
          "Everything still outstanding."],
         ["python run_pipeline.py --force",
          "Re-research contacts that already have results. Deliberate, and "
          "the reason the UI's Research button is not offered for a contact "
          "that already has results."],
         ["python run_pipeline.py --skip-interests",
          "Research only. No LLM calls at all."],
         ["python run_pipeline.py --skip-linkedin",
          "Skip the activity-link search."],
         ["python run_pipeline.py --only-linkedin",
          "Re-run just the activity search for every contact, without paying "
          "again for company and web research that already succeeded."]],
        [58 * mm, COL_W - 58 * mm]))


    # -------------------------------------------------------- 3. nine steps
    add(H1("The nine steps", "Section 3"))
    add([P("The pipeline the whole tool is organised around. Steps 4 to 7 are "
           "the ones that reach the network; the rest are local.", "lead")])
    add(Grid(
        ["#", "Step", "Where it lives, and how it works"],
        [["1", "<b>CSV upload</b>", "<font face=\"Courier\">app/importer.py</font> "
          "— alias-matched columns, sniffed delimiter, preamble skipped, "
          "unusable header rows repaired. Every tolerance is reported on the "
          "upload page rather than applied silently."],
         ["2", "<b>Parsing and normalization</b>", "75 Apollo columns down to "
          "the ~25 fields the UI actually uses. Rows are read positionally, so "
          "a duplicated column name can never swallow another column's data."],
         ["3", "<b>Missing-data enrichment</b>", "Gap detection per contact via "
          "<font face=\"Courier\">Person.missing_fields()</font>; the lookups "
          "themselves in <font face=\"Courier\">app/resolve.py</font>, with "
          "<font face=\"Courier\">app/employer.py</font> working out who "
          "someone works for when the CSV did not say."],
         ["4", "<b>LinkedIn activity</b>", "<font face=\"Courier\">app/research.py</font> "
          "— public post links from the search index, plus hand-pasted "
          "entries. LinkedIn is never crawled. Section 8."],
         ["5", "<b>Google / web research</b>", "<font face=\"Courier\">app/research.py</font> "
          "— two query angles, each result kept only if it can be tied to "
          "the contact. Section 9."],
         ["6", "<b>Interest detection</b>", "<font face=\"Courier\">app/interests.py</font> "
          "— one OpenAI-compatible call producing the chips and the focus "
          "line, both required to cite their evidence."],
         ["7", "<b>Company research</b>", "<font face=\"Courier\">app/research.py</font> "
          "— the company's own site preferred over anyone writing about "
          "them."],
         ["8", "<b>Dashboard</b>", "FastAPI plus server-rendered Jinja2. "
          "Interactivity is plain forms — no client framework, no build "
          "step."],
         ["9", "<b>Reports and export</b>", "<font face=\"Courier\">/reports</font> "
          "— breakdowns, and the stale-designation list."]],
        [7 * mm, 40 * mm, COL_W - 47 * mm]))

    add([P("One definition of “research a contact”", "h2")])
    add([P("The CLI and the Research button in the UI both call "
           "<font face=\"Courier\">app/pipeline.py:research_contact</font>. Two "
           "copies of that sequence would drift, and the one used less often "
           "would be the one that rotted. The same reasoning extracted "
           "<font face=\"Courier\">refresh_linkedin</font> when the standalone "
           "LinkedIn refresh was added.")])
    add([P("The button posts to "
           "<font face=\"Courier\">/person/&lt;slug&gt;/research</font>, parks "
           "the contact at <i>Researching…</i> and returns immediately — "
           "a run takes tens of seconds, and longer when the API rate-limits "
           "and the backoff waits, so the work happens in a thread. The form's "
           "<font face=\"Courier\">back</font> field is restricted to in-app "
           "paths, so the endpoint cannot be turned into an open redirect.")])


    # ------------------------------------------------------ 4. architecture
    add(H1("Architecture", "Section 4"))
    add([P("Server-rendered pages over a SQLite file, with two outbound HTTP "
           "dependencies. %d lines of Python across %d modules, and %d lines "
           "of Jinja2 templates."
           % (total_py, len([k for k in loc if k.startswith("app/")]),
              total_html), "lead")])

    add([P("The stack, and why", "h2")])
    add(Grid(
        ["Layer", "Choice", "Note"],
        [["Web framework", "FastAPI", "Dependency-injected sessions; the same "
          "app object serves HTML and the form posts."],
         ["Templates", "Jinja2, server-rendered", "Panels are macros in "
          "<font face=\"Courier\">templates/_partials.html</font>, so the "
          "dashboard and the person page render the identical component."],
         ["Interactivity", "Plain HTML forms", "One JS timer, polling a "
          "running LinkedIn refresh. Nothing else."],
         ["Styling", "Tailwind, prebuilt", "<font face=\"Courier\">static/app.css</font> "
          "is committed. No Node at runtime."],
         ["Database", "SQLite via SQLAlchemy", "Fine on a host with a "
          "persistent disk (Railway, Render, Fly). Not fine serverless — "
          "on Vercel the filesystem is ephemeral and writes vanish between "
          "requests. Point <font face=\"Courier\">app/db.py</font> at Postgres "
          "and nothing else changes."],
         ["Web research", "Firecrawl search API over HTTP", "One "
          "<font face=\"Courier\">_search</font> function with backoff; every "
          "caller goes through it."],
         ["Language model", "Any OpenAI-compatible endpoint", "No provider "
          "SDK, so hosted-to-self-hosted is a change to one environment "
          "variable."]],
        [26 * mm, 40 * mm, COL_W - 66 * mm]))

    add([P("Modules", "h2")])
    mod_rows = [
        ("app/main.py", "FastAPI app, every route, and the Jinja filters "
         "(relative time, short date, money, domain-of-URL)."),
        ("app/models.py", "The six tables, the status constants, and the "
         "derived properties the templates read."),
        ("app/db.py", "Engine, session factory, and a lightweight "
         "add-missing-columns migration on startup."),
        ("app/env.py", "Reads <font face=\"Courier\">.env</font> without a "
         "dependency; reports which keys are present."),
        ("app/importer.py", "CSV reading, header repair, alias column mapping, "
         "slug allocation, and the import report."),
        ("app/research.py", "Search, classification, corroboration, activity "
         "attribution and dating, employer naming, company research."),
        ("app/resolve.py", "Step 3 enrichment: find a missing email or "
         "LinkedIn URL, and refuse to when the evidence is ambiguous."),
        ("app/employer.py", "Works out the employer from title and posts when "
         "the CSV left it blank. Writes nothing."),
        ("app/pipeline.py", "The one definition of researching a contact, and "
         "of refreshing just the LinkedIn panel."),
        ("app/interests.py", "The chips and the focus line."),
        ("app/messages.py", "Recommended comment drafts, and the substance "
         "check that decides whether a draft is possible at all."),
        ("app/summary.py", "The composed profile paragraph, plus the markup "
         "cleaners the panels use on scraped text."),
        ("app/opportunities.py", "Capability matching, tiered by evidence, "
         "citing the field that fired."),
        ("app/segments.py", "Country and category, derived on read."),
        ("app/outreach.py", "The four-step sequence, its waits, and what "
         "blocks a step."),
    ]
    add(Grid(
        ["Module", "Responsibility", "Lines"],
        [[Paragraph("<font face=\"Courier\">%s</font>" % m, ST["cell"]),
          Paragraph(d, ST["cell"]),
          Paragraph(str(loc.get(m, "—")), ST["cell"])]
         for m, d in mod_rows],
        [41 * mm, COL_W - 41 * mm - 15 * mm, 15 * mm]))

    add([P("Scripts at the repository root", "h2")])
    add(Grid(
        ["Script", "What it is for"],
        [["run_pipeline.py", "The research CLI — see section 2."],
         ["seed_research.py", "Loads the researched sample state used in the "
          "screenshots, so the dashboard can be seen full without spending "
          "API credits."],
         ["backfill_activity_dates.py", "One-off: decodes the timestamp out of "
          "each activity URL's id and fills "
          "<font face=\"Courier\">activity_date</font> for rows stored before "
          "dating existed."],
         ["purge_linkedin_web_findings.py", "One-off: removes the LinkedIn "
          "rows that the web panel had collected before corroboration was "
          "tightened."],
         ["tools/build_project_pdf.py", "Builds this document."]],
        [54 * mm, COL_W - 54 * mm]))
    add([P("The two one-off scripts are kept rather than deleted because each "
           "took a backup first — the "
           "<font face=\"Courier\">data/peopleintel.backup-before-*.db</font> "
           "files are those backups, one per behaviour change that rewrote "
           "stored rows.", "note")])


    add([P("Pages and routes", "h2")])
    add(Grid(
        ["Page", "Route", "What is on it"],
        [["Home", "GET /", "A selected contact rendered in full — profile "
          "summary, interests, company, opportunities, LinkedIn activity and "
          "web findings — above the people list and the upload panel."],
         ["People", "GET /people", "The list, filterable by status, country and "
          "category, with the Research button on any contact that has not been "
          "researched."],
         ["Person", "GET /person/{slug}", "Everything known about one contact, "
          "the paste form for an activity, the drift warnings, and their "
          "outreach steps."],
         ["Companies", "GET /companies, /company/{id}", "Employers, their "
          "researched description with its source and fetch date, and their "
          "people."],
         ["Enrichment", "GET /enrichment", "Who is missing what, critical gaps "
          "separated from optional ones."],
         ["Research", "GET /research", "Research status across the list."],
         ["Reports", "GET /reports", "Breakdowns, and the stale-designation "
          "list."],
         ["Outreach", "GET /outreach", "The board: what is due today, what is "
          "overdue, what is waiting."],
         ["Settings", "GET /settings", "Which environment keys are present. "
          "Never their values."],
         ["Upload", "GET /upload, POST /upload", "The CSV form, and the import "
          "report."]],
        [24 * mm, 42 * mm, COL_W - 66 * mm]))

    add([P("Form endpoints", "h2")])
    add(Bullets([
        "<font face=\"Courier\">POST /person/{slug}/research</font> — "
        "research one contact, in a thread.",
        "<font face=\"Courier\">POST /person/{slug}/refresh-linkedin</font> "
        "— re-run just the activity search.",
        "<font face=\"Courier\">POST /person/{slug}/suggest-comments</font> "
        "— draft comments for their posts.",
        "<font face=\"Courier\">POST /person/{slug}/activity</font> and "
        "<font face=\"Courier\">POST /activity/{id}/delete</font> — paste "
        "an activity by hand, or remove one.",
        "<font face=\"Courier\">POST /person/{slug}/outreach/start</font>, and "
        "<font face=\"Courier\">/outreach/{step_id}/</font>"
        "<font face=\"Courier\">{done,skip,reschedule}</font> — move a "
        "contact through the sequence.",
    ]))


    # -------------------------------------------------------- 5. data model
    add(H1("The data model", "Section 5"))
    add([P("Six tables. The design rule, carried from the first plan: every "
           "fact that was fetched from the web stores where it came from and "
           "when. A field that came straight from the CSV carries no "
           "provenance, because the file is the source.", "lead")])
    add(Grid(
        ["Table", "Rows", "What it holds"],
        [["companies", str(facts.get("companies", "—")),
          "The employer as the CSV gave it — name, website, industry, "
          "headcount, location, revenue, funding, technologies, keywords — "
          "plus the researched <font face=\"Courier\">description</font> with "
          "its source URL and fetch time."],
         ["people", str(facts.get("people", "—")),
          "Identity, contact details, location, both statuses, and the "
          "observed-versus-filed pairs for designation and LinkedIn profile."],
         ["linkedin_activities", str(facts.get("activities", "—")),
          "One post, repost, comment or tagged mention. Carries its type, URL, "
          "text, date, display rank, whether it was pasted or found by search, "
          "the query that found it, its corroboration, and the recommended "
          "comment draft."],
         ["web_findings", str(facts.get("findings", "—")),
          "One search result: title, URL, snippet, kind, rank, the query, "
          "whether it was corroborated and by what, and whether it is "
          "company-news filler rather than about the person."],
         ["interests", str(facts.get("interests", "—")),
          "One chip: its label, the evidence that produced it, and whether "
          "that evidence was LinkedIn, the web, or both."],
         ["outreach_steps", str(facts.get("steps", "—")),
          "One step of the sequence for one contact: due date, done or skipped "
          "and when, the skip reason, and a note recording what was actually "
          "done."]],
        [34 * mm, 13 * mm, COL_W - 47 * mm]))

    add([P("Both copies, never one", "h2")])
    add([P("Two fields on <font face=\"Courier\">Person</font> exist in pairs "
           "because the CSV's copy goes stale and the app's answer is not "
           "automatically better:")])
    add(Grid(
        ["From the file", "Found on the web", "Flagged as"],
        [["<font face=\"Courier\">title</font>",
          "<font face=\"Courier\">title_observed</font>, with its source and "
          "the date it was seen",
          "<font face=\"Courier\">title_drift</font> — compared loosely, "
          "so “Director, Content” and “director content” "
          "are the same title"],
         ["<font face=\"Courier\">linkedin_url</font>",
          "<font face=\"Courier\">linkedin_observed</font>, the profile they "
          "actually post from",
          "<font face=\"Courier\">linkedin_drift</font> — compared on the "
          "slug, so http/https, www and a trailing slash do not differ"]],
        [38 * mm, 55 * mm, COL_W - 93 * mm]))
    add([P("Neither overwrites the file. The person page says so where they "
           "disagree, and <font face=\"Courier\">/reports</font> lists every "
           "designation mismatch in one place.", "note")])

    add([P("Derived on read, never stored", "h2")])
    add([P("A property that can be computed from the record is a property, not "
           "a column — so a re-upload or an enrichment run cannot leave a "
           "stale copy behind. <font face=\"Courier\">profile_summary</font>, "
           "<font face=\"Courier\">opportunities</font>, "
           "<font face=\"Courier\">category</font>, "
           "<font face=\"Courier\">segment_countries</font>, "
           "<font face=\"Courier\">outreach_stage</font>, "
           "<font face=\"Courier\">title_drift</font> and "
           "<font face=\"Courier\">linkedin_drift</font> are all computed when "
           "the page asks for them.")])
    add([P("One legacy column is kept deliberately: "
           "<font face=\"Courier\">Person.status</font>, which nothing in the "
           "app reads any more, is held in step with "
           "<font face=\"Courier\">enrichment_status</font> so that an older "
           "query or export does not silently read a frozen value.", "note")])


    # ------------------------------------------------------- 6. two statuses
    add(H1("Two statuses, not one", "Section 6"))
    one(Callout(
        "The bug that produced the rule",
        ["One field answered two questions, so “Needs Enrichment” "
         "came to mean “something, somewhere, is missing” — "
         "which is true of almost every contact and therefore says nothing "
         "about any of them. It fired when research had not run yet, or when a "
         "company had no description, and flagged 16 of 25 contacts.",
         "A fully identified person who has not been researched is not missing "
         "data about themselves. A person with no email is, whether or not "
         "research has run."],
        accent=AMBER, bg=AMBER_PALE))
    add(Grid(
        ["Field", "Question it answers", "Values"],
        [["<font face=\"Courier\">enrichment_status</font>",
          "Do we know who this person is? Set from identity fields only: "
          "email, LinkedIn URL, designation, and the employer's website. Each "
          "is a thing enrichment could go and fill. Nothing else counts.",
          "Complete · Needs Enrichment"],
         ["<font face=\"Courier\">research_status</font>",
          "Has anyone looked them up? A failed search no longer overwrites the "
          "enrichment verdict — the two are independent.",
          "Not researched → Researching… → Researched, or "
          "Research failed"]],
        [38 * mm, COL_W - 38 * mm - 40 * mm, 40 * mm]))
    add([P("Because they are independent, <b>Needs Enrichment + Researched</b> "
           "is a real, expressible state, and so is <b>Complete + Not "
           "researched</b>. Both are visible in the live data (section 14).")])

    add([P("Explicitly not enrichment blockers", "h2")])
    add(Grid(
        ["Gap", "Still shown as", "Why it does not block"],
        [["Direct phone", "an optional gap",
          "The Apollo export carries one for nobody, so counting it would hold "
          "every contact at Needs Enrichment forever."],
         ["Company description", "an optional gap",
          "A fact about the employer, and one that research fills in — "
          "not something missing from this person's record."],
         ["Web research, LinkedIn activity", "an optional gap",
          "Their absence means nobody has looked yet, or there was nothing "
          "public to find. That is research status."]],
        [40 * mm, 30 * mm, COL_W - 70 * mm]))
    add([P("Both statuses are decided in exactly one place, "
           "<font face=\"Courier\">Person.recompute_status()</font>. Five call "
           "sites previously each ran their own copy of the rule, so a change "
           "to it had to be made five times. The method also guards a run in "
           "progress: without that guard an import or an activity edit "
           "mid-run would reset the badge to “Not researched” and the "
           "button would offer to start a second run.")])
    add([P("Designation counts as critical because it is one of the three "
           "pillars and it comes from the file, so a blank one is a real hole. "
           "Dropping it from "
           "<font face=\"Courier\">missing_critical()</font> is a one-line "
           "change and nothing else has to move.", "note")])

    add([P("Two buttons, two different rules", "h2")])
    add(Grid(
        ["", "Research", "Refresh LinkedIn"],
        [["Offered when", "Nothing found yet, or the last attempt failed.",
          "The first research run has completed at least once, and no run of "
          "either kind is in flight."],
         ["Offered again after success?", "<b>No.</b> Re-running spends API "
          "credits, so that is a deliberate "
          "<font face=\"Courier\">run_pipeline.py --force</font>, not "
          "something a stray double-click can do.",
          "<b>Yes, every time.</b> Activity is the one genuinely "
          "time-sensitive panel here — LinkedIn's feed moves daily, so a "
          "search-found list from weeks ago goes stale in a way the rest of "
          "the record does not."],
         ["Progress shown by",
          "<font face=\"Courier\">research_status = Researching…</font>, "
          "the whole record.",
          "<font face=\"Courier\">linkedin_refreshing</font>, a separate flag, "
          "so refreshing one panel does not flip the whole record back to "
          "“Researching…”. A JS timer polls it."]],
        [34 * mm, (COL_W - 34 * mm) / 2, (COL_W - 34 * mm) / 2]))


    # ---------------------------------------------------------- 7. importer
    add(H1("What the CSV importer tolerates", "Section 7"))
    add([P("The 75-column Apollo export is the happy path, but the file that "
           "arrives is often not that. Each tolerance below is reported on the "
           "upload page rather than applied silently.", "lead")])
    add(Grid(
        ["Case", "Handled how"],
        [["<b>Delimiters</b>", "Comma, semicolon (Excel under a European "
          "locale), tab, pipe. Sniffed from the file's content, not its "
          "extension."],
         ["<b>Encodings</b>", "UTF-8, UTF-8 with BOM, UTF-16 (Excel's "
          "“Unicode Text”), Latin-1."],
         ["<b>A preamble</b>", "Lines above the header row — "
          "<i>Exported from CRM on …</i> — are skipped by finding the "
          "real header."],
         ["<b>Other exports</b>", "LinkedIn Sales Navigator, HubSpot, "
          "Salesforce, and a plain <font face=\"Courier\">Name,Email</font> "
          "file all import. Columns are matched by alias, so <i>Associated "
          "Company</i>, <i>Account Name</i> and <i>Company</i> all mean the "
          "same field."],
         ["<b>A broken header row</b>", "An export sometimes arrives with its "
          "leading block of column names overwritten by one repeated label "
          "— 17 columns all called <i>First Name</i>. A repeated label "
          "names none of its columns, so those positions are recovered from "
          "the Apollo layout by aligning on the labels that <i>are</i> "
          "trustworthy. Below 60% agreement the repair is refused rather than "
          "guessed at, and where a label still repeats, the first column "
          "carrying it is the one that counts."]],
        [34 * mm, COL_W - 34 * mm]))
    one(Callout(
        "Rows are read positionally",
        "Never by column name. A duplicated column name can therefore never "
        "silently swallow another column's data — which is the failure "
        "mode the header repair above exists to survive in the first place."))

    add([P("Then 75 columns become 25", "h2")])
    add([P("Step 2 keeps only the fields the UI uses, splitting them across "
           "the employer and the person and coercing the numeric ones "
           "(headcount, revenue, funding). Each new contact also gets a unique "
           "slug, allocated against the slugs already in the database so that "
           "two people with the same name get distinct, stable URLs.")])


    # ---------------------------------------------------------- 8. linkedin
    add(H1("LinkedIn, without logging in", "Section 8"))
    one(Callout(
        "No credentials, no scraper, no data provider",
        "This app has no LinkedIn credentials, no scraper, and no third-party "
        "LinkedIn data provider. It never logs into LinkedIn and never fetches "
        "a linkedin.com page. Activities reach the panel two ways, and every "
        "row says which."))
    add(Grid(
        ["Route", "How the row arrives", "Rows now"],
        [["<b>found by search</b>",
          "The pipeline and the Research button ask the same web-search index "
          "used for everything else for public post URLs — "
          "<font face=\"Courier\">site:linkedin.com/posts \"Name\"</font>. "
          "Each row keeps the query and the fetch time, the same provenance a "
          "web finding carries.",
          str(facts.get("searched", "—"))],
         ["<b>pasted</b>",
          "The form on the contact's page: pick the type, paste the link and "
          "the text. A hand-pasted row is trusted outright — a person "
          "judged it — and is never overwritten by a later search run; "
          "the search only fills the slots left over, up to five in total.",
          str(facts.get("pasted", "—"))]],
        [30 * mm, COL_W - 30 * mm - 18 * mm, 18 * mm]))

    add([P("What proves a post is theirs", "h2")])
    add([P("A post URL is "
           "<font face=\"Courier\">/posts/&lt;author-slug&gt;_&lt;words-from-"
           "the-post&gt;-activity-&lt;id&gt;</font>, so the author segment is "
           "the only thing in a search result that can prove authorship. A row "
           "is kept only when one of three things ties it to <i>this</i> "
           "contact:")])
    add(Numbered([
        "<b>The author slug is a profile we hold for them</b>, compared "
        "exactly. Not as a substring: the short slug "
        "<font face=\"Courier\">anwar-chaudhry</font> is itself a substring of "
        "<font face=\"Courier\">dr-mumtaz-anwar-chaudhry-98231b11</font>, and "
        "a post's own text-slug contains the names of everyone it talks about "
        "— so substring matching credited a hospital's post <i>about</i> a "
        "Professor Anwar Chaudhry to ours.",
        "<b>The author is a different profile carrying their name, and the "
        "post names their employer.</b> Two independent facts — the same "
        "pair <font face=\"Courier\">resolve.py</font> accepts as proof that a "
        "LinkedIn URL belongs to someone.",
        "<b>Someone else's post that names them with their job title</b>, "
        "where the author is the employer's own page, or where name, employer "
        "and title all three appear. That is the three-fact test the web panel "
        "already uses.",
    ]))
    add([P("Name alone is never enough.", "h3")])
    add([P("Matching on name tokens credited five different Laura MacLeods' "
           "posts to ours. A middle version that showed name-matched rows "
           "behind an “unverified” badge was built and then reverted: "
           "of 49 rows across 15 contacts, 7 were the actual contact. One "
           "panel showed four different Christopher Carrolls; another showed a "
           "recruiter called Daryl Daley, matched because “daryl” and "
           "“speed” appear in that order in “Great insight, "
           "Daryl! speed with clarity”. A labelled wrong row is still a "
           "wrong row on someone's profile.")])

    add([P("Why rule 2 exists", "h2")])
    add([P("Requiring the CSV's slug meant the panel was empty whenever the "
           "export's LinkedIn URL was not the profile the person posts from "
           "— and vanity URLs get changed, so exports go stale. Julie "
           "LeBrun posts weekly about OCA training from "
           "<font face=\"Courier\">julie-lebrun-tumbaoju</font> while the file "
           "records <font face=\"Courier\">julie-lebrun-45583110b</font>; her "
           "panel showed nothing, which reads as “she does not post” "
           "rather than “our URL for her is wrong”.")])
    add([P("When rule 2 fires, the profile it found is stored in "
           "<font face=\"Courier\">linkedin_observed</font> and the contact's "
           "page says the file's URL points elsewhere — because the header "
           "link and the outreach board's “Open their LinkedIn” both "
           "go to the wrong profile until someone fixes it. A contact with no "
           "<font face=\"Courier\">linkedin_url</font> at all is not excluded; "
           "it just narrows them to rules 2 and 3.")])


    add([P("Ordering the panel", "h2")])
    add([P("Kind first, then recency inside a kind: the contact's own readable "
           "posts, then anything else they wrote, then anything readable, then "
           "the rest — newest first throughout, with the date decoded from "
           "the activity id embedded in each URL.")])
    add([P("Recency alone was right while every row was a post they wrote. "
           "Once posts that merely name them are admitted it is not: those "
           "arrive in volume and are mostly LinkedIn's attribution block with "
           "a fresh date on it, so recency alone ranked a content-free "
           "February mention above Gordon Hirons' own announcement of the IB "
           "Science Questionbanks.")])

    add([P("Snippets that say nothing", "h2")])
    add([P("When a contact comments under someone else's post, what the index "
           "returns is often just LinkedIn's furniture — <i>“Micaela "
           "Metz, graphic · Micaela Metz. Senior Learning Content Manager "
           "@ Axonify. 1y. Report this comment”</i>. Strip the chrome, "
           "their name, their title and their employer, and nothing is left.")])
    add([P("Those rows keep their link, type and date and lose their text, "
           "because text is what interest chips are derived from — and a "
           "chip derived from that snippet would be a chip derived from a job "
           "title.")])

    one(Callout(
        "Two honest limits on the searched route",
        ["It sees only what a search engine has already indexed, which for a "
         "mid-level contact is often nothing. The panel says <b>“Nothing "
         "indexed”</b> rather than padding to reach five.",
         "The text is the indexed snippet, not the whole post. Where no "
         "snippet exists the row is kept as a link and labelled as one — "
         "and that label matters, because a bare URL gives the interest model "
         "nothing to work with. Pasting the real text by hand is still worth "
         "doing."],
        accent=AMBER, bg=AMBER_PALE))

    # ---------------------------------------------------- 9. web research
    add(H1("Web research and corroboration", "Section 9"))
    add([P("A search for a name returns namesakes. Searching <i>“Laura "
           "Macleod” Nelson</i> returned her real LinkedIn profile — "
           "and a Maine phone listing, a freelance creative strategist, a "
           "wedding-group singer, and a 1994 SAGE paper on teaching "
           "performance appraisals. Four different people.", "lead")])
    add([P("So a result is kept only when one of three things is true:")])
    add(Numbered([
        "It is on the employer's own domain, or a subdomain of it.",
        "It is the contact's own LinkedIn profile, matched on the slug from "
        "the CSV.",
        "Its text carries their name, their job title <b>and</b> their "
        "employer, together.",
    ]))
    add([P("Signal 3 exists because 1 and 2 alone were too narrow: <i>“Nora "
           "Mawla. Content Marketing Manager, Corndel.”</i> is "
           "unmistakably her, and was being discarded for the sole reason that "
           "it was not hosted on corndel.com. Three CSV facts co-occurring is "
           "not a coincidence a namesake produces. A name alone is not a "
           "signal, and neither is a name plus an employer — "
           "“Nelson” matched a post written by a Sean Nelson.")])
    one(Callout(
        "Anything that clears none of the three is not stored",
        ["An earlier version kept them behind an “identity "
         "unconfirmed” badge, on the theory that a labelled lead beats an "
         "empty panel. It does not: the label puts the work of re-checking "
         "every row back on the reader, and a stranger's phone listing sitting "
         "on someone's profile is wrong whatever it is wearing.",
         "The cost is real and was measured. Of 85 findings re-scored under "
         "this rule, 18 were recovered by signal 3 and 36 were dropped — "
         "and a few of those were genuinely relevant, like a conference-talk "
         "listing that simply never repeats the speaker's title and employer "
         "in its indexed snippet. Precision was chosen over recall "
         "deliberately. Loosen signal 3 in "
         "<font face=\"Courier\">research._corroborate</font> to trade back "
         "the other way."]))

    add([P("Coverage is uneven, and that is the data", "h2")])
    add([P("A senior, conference-speaking contact returns four or five solid "
           "findings. A mid-level Content Manager often returns only their own "
           "LinkedIn profile. Of the 25 contacts in the sample export, 13 are "
           "Manager-level — thin panels are expected for most of them. "
           "Empty panels say “Not found” and stay empty.")])
    add([P("One narrow exception exists: where the person searches turned up "
           "too little, company news can fill the panel, stored with "
           "<font face=\"Courier\">about_company = true</font> and never "
           "corroborated. By construction it can never derive an interest chip "
           "or an inferred employer — both already skip anything "
           "uncorroborated — it only keeps the panel from sitting empty, "
           "and it says on its face that it is about the employer.", "note")])

    add([P("Designations go stale fast", "h2")])
    add([P("Of the four contacts researched early in the sample, <b>three</b> "
           "had a different job title on the web than in the file:")])
    add(Grid(
        ["Person", "In the CSV", "On the web"],
        [["Laura Macleod", "Executive Director, Content Services",
          "<b>Vice President</b>, Content Services"],
         ["Joshua Dyer", "Director, Content &amp; Community Engagement",
          "Director, <b>Strategy &amp; Engagement</b>"],
         ["Angelina Attisano", "Content Manager",
          "Communications &amp; Administrative Professional"]],
        [34 * mm, (COL_W - 34 * mm) / 2, (COL_W - 34 * mm) / 2]))
    add([P("Designation is one of the three pillars and it ages faster than "
           "anything else in the row. Mismatches are flagged on the contact's "
           "page and listed on <font face=\"Courier\">/reports</font>. Both "
           "values are kept — the tool neither silently overwrites the "
           "file nor silently trusts it.", "note")])


    # ------------------------------------------------- 10. the LLM sections
    add(H1("Interests, focus lines and comment drafts", "Section 10"))
    add([P("These are the only inference steps in the project, and they are "
           "written against the OpenAI-compatible chat-completions format "
           "— no provider SDK — so the same code runs on Groq now and "
           "a self-hosted Ollama or vLLM later by changing "
           "<font face=\"Courier\">LLM_BASE_URL</font>.", "lead")])

    add([P("Interest chips and the focus line", "h2")])
    add([P("The panel is the most trust-sensitive part of the page, so the "
           "prompt enforces two rules and the code enforces them again:")])
    add(Bullets([
        "<b>Every chip must cite the evidence it came from.</b> A chip with no "
        "evidence is a guess dressed as a fact, and one arriving with an empty "
        "evidence field is dropped in "
        "<font face=\"Courier\">app/interests.py</font> rather than displayed. "
        "So no chip on the page is unsourced — hover one to see the post "
        "or article that produced it.",
        "<b>Thin evidence returns nothing.</b> "
        "<font face=\"Courier\">MIN_EVIDENCE</font> is the floor: fewer "
        "signals than that and the step does not guess. An honest empty panel "
        "beats four plausible chips invented from a job title.",
        "Only corroborated rows are eligible as evidence in the first place, "
        "and what the person themselves said is preferred over what others "
        "wrote about them.",
        "Chips are 1–3 word noun phrases, at most five. The focus line is "
        "one or two sentences about what they are actually working on, or "
        "<font face=\"Courier\">null</font> when the evidence is too thin.",
    ]))

    add([P("The composed summary, for contrast", "h2")])
    add([P("The profile paragraph on every contact is <b>composed, not "
           "generated</b>. Every clause restates a field the app already holds "
           "— title, seniority, department, company, industry, headcount "
           "— so it exists for every contact the moment a CSV lands, costs "
           "nothing, and cannot invent. That matters more here than fluency: "
           "those fields are present for everyone, while the focus line is "
           "present for %s of %s. A generated summary would have left most of "
           "the dashboard blank."
           % (facts.get("focus", "—"), facts.get("people", "—")))])
    add([P("Research is folded in when it exists and skipped silently when it "
           "does not, so the paragraph gets richer as the pipeline runs rather "
           "than appearing late. <font face=\"Courier\">basis</font> names the "
           "fields behind each sentence, so the panel shows its sources the "
           "way every other panel does. And <b>no pronoun is ever "
           "guessed</b> — a name does not tell you how someone refers to "
           "themselves, so the subject is the person's name and then "
           "“they”.")])

    add([P("Recommended comment drafts", "h2")])
    add([P("Steps one and two of the outreach sequence are “comment on "
           "their post”, and a task that says only that leaves the actual "
           "work undone. So the draft lives beside the post it replies to, for "
           "a person to edit and send. Nothing is posted automatically.")])
    add([P("The hard constraint is the source text.", "h3")])
    add([P("An activity row's text is the search index's snippet of a LinkedIn "
           "page, and for most rows that snippet is page furniture rather than "
           "the post: <i>“Nora Mawla Content Marketing Manager "
           "Videographer Content Creator 8mo”</i>. Of the 17 rows on hand "
           "when this was built, 7 carried something a comment could reference "
           "and 10 carried the person's own headline and nothing else.")])
    add([P("So substance is checked before anything is generated. A draft "
           "written from a headline would be a confident comment about nothing "
           "— the exact failure the rest of the app refuses elsewhere "
           "— and it would go out under the user's name. Rows that fail "
           "the check say so, and point at the paste form, which is the actual "
           "fix: pasting the real post text makes a good draft possible.")])
    one(Callout(
        "The output is checked in code, not merely requested in the prompt",
        "Flattery openers, pitches, links and hashtags are rejected outright, "
        "because a comment carrying any of them reads as automated — and "
        "that defeats the entire point of a hand-written sequence. "
        "<font face=\"Courier\">suggested_note</font> carries the reason when "
        "no draft was possible, so the panel explains itself rather than "
        "sitting empty."))


    # ---------------------------------------------------- 11. opportunities
    add(H1("Opportunities", "Section 11"))
    add([P("What can be offered to the contact on screen. Screwdriver's four "
           "capabilities are matched against signals already on the record. "
           "Nothing is generated and nothing is guessed — a capability "
           "appears only when a term for it is actually present in the "
           "person's own words, their designation, or their employer's record, "
           "and the row names the exact field it fired on and quotes the words "
           "around the match.", "lead")])
    add(Grid(
        ["Capability", "What is offered", "Example terms it fires on"],
        [["<b>AI &amp; Automation</b>",
          "AI-assisted content pipelines — auto-tagging, transcription and "
          "repurposing of the library they already own.",
          "ai, machine learning, automation, agentic, llm, generative, copilot"],
         ["<b>Video &amp; Multimedia</b>",
          "Production capacity, scripted, edited and versioned for their "
          "channels, without standing up an in-house studio.",
          "video, videography, multimedia, podcast, film, animation, webinar"],
         ["<b>Custom Software</b>",
          "The portals, internal tools and integrations that sit between their "
          "content and the people meant to use it.",
          "platform, software, engineering, integration, lms, portal, api, saas"],
         ["<b>Learning Design</b>",
          "Instructional design and production for their programmes, from "
          "outline to published module.",
          "learning, e-learning, curriculum, certification, instructional, "
          "pedagogy, upskilling"]],
        [30 * mm, (COL_W - 30 * mm) * 0.52, (COL_W - 30 * mm) * 0.48]))

    add([P("Three tiers, because the evidence is not equally about the person",
           "h2")])
    add(Grid(
        ["Tier", "Source", "What the row says, and its caveat"],
        [["<b>said</b>", "Their own posts, interests or focus line.",
          "<i>“They raised it themselves”</i> — the only tier "
          "that reflects this person's stated priorities."],
         ["<b>role</b>", "Their designation and department, from the CSV.",
          "<i>“It sits in their remit”</i> — solid about what "
          "they own, but a job description is not an intent."],
         ["<b>company</b>", "The employer's industry, description and keywords.",
          "<i>“Context at their employer”</i> — weakest: true of "
          "the employer, not necessarily of the person reading the pitch."]],
        [22 * mm, 48 * mm, COL_W - 70 * mm]))
    add([P("The tier is shown on every row, so a reader can tell “they "
           "said this” from “their employer is in this sector” "
           "without opening anything. An unmatched capability is left out "
           "rather than padded in — a plausible-looking pitch nobody can "
           "trace is worse than a shorter list.")])
    one(Callout(
        "Matching is per field, never against a merged blob",
        ["An earlier version concatenated a contact's industry, description "
         "and CSV keywords and searched that. It found the right term but then "
         "cited the wrong source — <i>“Hibernia College works in this "
         "space — e-learning”</i> for a term that had in fact matched "
         "in the keywords column. A citation that points at the wrong field is "
         "worse than no citation, because it looks checkable and is not.",
         "Two smaller details follow from the same care: terms match on word "
         "boundaries, so <i>ai</i> must be the word “ai” rather than "
         "firing on <i>said</i>, <i>email</i> and <i>training</i>; and the "
         "quoted excerpt is centred on the match, because truncating from the "
         "start would routinely cut off the very word that justified the row."]))

    # -------------------------------------------------------- 12. segments
    add(H1("Segmenting the list", "Section 12"))
    add([P("Two independent filters. Either one alone narrows the list, and "
           "together they intersect — “Education in Canada”. "
           "Neither is stored on the record; both are derived on read from what "
           "the CSV already gave us, so a re-upload or an enrichment run never "
           "leaves a stale segment behind.", "lead")])

    add([P("Country — either country matches", "h2")])
    add([P("An Apollo export carries two countries, and they are not the same "
           "fact: where the person is, and where their employer is. On the "
           "list in hand the person's country is Canada for 25 of 27 rows "
           "while the employer's spans seven countries, so filtering on the "
           "person alone would produce one useless bucket. A contact therefore "
           "matches a country when <i>either</i> field matches, and both "
           "countries are shown on the row so a match is never mysterious.")])
    if facts.get("countries"):
        add([P("Employer countries in the current data: "
               + ", ".join("%s (%d)" % (c or "unknown", n)
                           for c, n in facts["countries"]) + ".", "note")])

    add([P("Category — keyword-mapped, first match wins", "h2")])
    add([P("Nothing in the file says “education” or "
           "“medical”. The nearest thing is the employer's industry, "
           "which arrives as free text — <i>e-learning</i>, <i>higher "
           "education</i>, <i>pharmaceuticals</i>, <i>electrical/electronic "
           "manufacturing</i> — so those are mapped into eight broad "
           "buckets by keyword.")])
    add(Grid(
        ["Bucket", "Matched on terms like"],
        [["Education", "e-learning, edtech, university, curriculum, training, "
          "courseware, pedagogy"],
         ["Medical &amp; Health", "medical, hospital, pharmaceutical, biotech, "
          "clinical, nursing, diagnostic"],
         ["Engineering &amp; Technology", "engineering, software, "
          "semiconductor, manufacturing, robotics, saas, cloud, cyber"],
         ["Finance &amp; Insurance", "financial, bank, insurance, investment, "
          "accounting, fintech, wealth"],
         ["Nonprofit &amp; Public", "nonprofit, charity, civic, government, "
          "public administration, ngo, foundation"],
         ["Media &amp; Creative", "media, publishing, film, broadcast, "
          "advertising, design, journalism"],
         ["Retail &amp; Consumer", "retail, consumer, apparel, food, "
          "hospitality, tourism, e-commerce"],
         ["Other", "Matched nothing above."]],
        [46 * mm, COL_W - 46 * mm]))
    add([P("Order is precedence, so <i>professional training &amp; "
           "coaching</i> has to reach Education before anything else claims "
           "it. When the industry is blank, the company's keywords and then "
           "the person's own title are tried — a row with no industry "
           "still often says what it does, and a title of <i>“Filmmaker. "
           "Creative Director. Founder”</i> is clearly Media. A row that "
           "matches nothing lands in <b>Other</b> rather than being hidden: an "
           "unmatched contact is still a contact, and silently dropping it "
           "from every category view would quietly shrink the list.")])


    # -------------------------------------------------------- 13. outreach
    add(H1("The outreach sequence", "Section 13"))
    add([P("Comment, wait, comment, connect, email. Every step is done by "
           "hand: nothing here posts a comment, sends a connection request or "
           "sends an email. The app tracks where each contact is and what is "
           "due today, and a person does the work.", "lead")])
    one(Callout(
        "Why it is manual on purpose",
        "Automated LinkedIn engagement gets accounts restricted — and an "
        "automated first touch is exactly the thing this sequence is designed "
        "not to look like. The one piece of help the tool offers is a comment "
        "draft (section 10), which a person still edits and sends."))
    add(Grid(
        ["Step", "Wait", "Counted from", "What it is"],
        [["<b>1 · Comment on their post</b>", "0 days",
          "entering the sequence",
          "Pick a recent post from the LinkedIn Activity panel and leave "
          "something specific. The panel is ordered newest first."],
         ["<b>2 · Comment again</b>", "2 days", "step 1 completed",
          "The two-day gap, so the second comment is not same-day."],
         ["<b>3 · Connect</b>", "0 days", "step 2 completed",
          "Straight after the second comment, while they may still recognise "
          "the name from the comment thread."],
         ["<b>4 · Email</b>", "2 days", "step 3 completed",
          "Two days after the connection request."]],
        [40 * mm, 14 * mm, 30 * mm, COL_W - 84 * mm]))
    add([P("Those are defaults, not rules. A due date can be moved, and a step "
           "can be skipped with a reason when there is nothing to comment on.",
           "note")])

    add([P("Why steps are created one at a time", "h2")])
    add([P("A manual sequence cannot schedule step three in advance, because "
           "its due date depends on the day step two <i>actually</i> happened "
           "rather than the day it was supposed to. So a step is created when "
           "the one before it is completed, with its due date measured from "
           "that completion — marking a step done is therefore also what "
           "schedules the next one.")])
    add([P("Exactly one step per contact is open at a time, which is what "
           "makes “today's tasks” a simple query rather than a "
           "scheduling problem. A step is open until it is either done or "
           "skipped, and reports one of five states: <b>waiting</b>, "
           "<b>due</b>, <b>overdue</b>, <b>done</b>, <b>skipped</b>. Each step "
           "also carries a note recording what was actually done — which "
           "post was commented on, what the email said — because the "
           "sequence is only useful on the second pass if it records that.")])
    add([P("A step can also be <b>blocked</b>: the comment steps need a "
           "LinkedIn activity to comment on, and the email step needs an email "
           "address. <font face=\"Courier\">blocked_reason</font> is a "
           "property on the step rather than a template filter, so the board "
           "and the person page get the same answer without either registering "
           "anything.", "note")])

    # ------------------------------------------------------- 14. snapshot
    add(H1("Where the data stands", "Section 14"))
    if not facts.get("available"):
        add([P("No database found at "
               "<font face=\"Courier\">data/peopleintel.db</font>, so this "
               "section is empty. Import a CSV and rebuild.", "note")])
    else:
        add([P("Read from <font face=\"Courier\">data/peopleintel.db</font> at "
               "build time. This is the sample Apollo export plus whatever "
               "research has been run against it.", "lead")])
        add(Tiles([
            (str(facts["people"]), "contacts"),
            (str(facts["companies"]), "companies"),
            (str(facts["complete"]), "identity complete"),
            (str(facts["researched"]), "researched"),
        ]))
        add(Tiles([
            (str(facts["activities"]), "LinkedIn activities"),
            (str(facts["findings"]), "web findings"),
            (str(facts["interests"]), "interest chips"),
            (str(facts["focus"]), "focus lines"),
        ]))
        add([P("What the numbers say", "h2")])
        add(Bullets([
            "<b>Both status combinations are present.</b> %d contacts are "
            "identity-complete and researched, %d are complete but not "
            "researched yet, and %d researched while still missing an "
            "identity field — the state a single combined status could "
            "not express (section 6)."
            % (facts["complete_researched"], facts["complete_pending"],
               facts["needs_researched"]),
            "<b>Every stored web finding is corroborated:</b> %d of %d. That "
            "is not a coincidence — anything that cleared none of the "
            "three signals was never stored (section 9)."
            % (facts["corroborated"], facts["findings"]),
            "<b>%d contacts have at least one web finding</b> and %d have at "
            "least one LinkedIn activity, out of %d. The gap is the uneven "
            "coverage described in section 9, not a failure."
            % (facts["with_findings"], facts["with_activity"], facts["people"]),
            "<b>%d of %d activities were found by search</b>, %d pasted by "
            "hand. Types: %s."
            % (facts["searched"], facts["activities"], facts["pasted"],
               ", ".join("%d %s" % (n, t) for t, n in facts["act_types"])),
            "<b>%d contacts show a designation on the web</b>, which is where "
            "the drift flags and the "
            "<font face=\"Courier\">/reports</font> stale list come from. "
            "%d shows a different posting profile than the file records."
            % (facts["drift"], facts["li_drift"]),
            "<b>%d focus lines and %d interest chips</b> across %d contacts "
            "— the rest returned nothing rather than guessing, which is "
            "the intended behaviour when evidence is thin."
            % (facts["focus"], facts["interests"], facts["people"]),
            "<b>%d comment drafts</b> exist. The rest of the activity rows "
            "failed the substance check, and say so (section 10)."
            % facts["drafts"],
            "<b>%d outreach steps</b> have been created. Steps are created one "
            "at a time as the sequence progresses, so this counts real "
            "progress rather than a plan." % facts["steps"],
        ]))


    # -------------------------------------------------------- 15. history
    add(H1("Build history", "Section 15"))
    if commits:
        add([P("%d commits, oldest last. Each one is a behaviour change rather "
               "than a checkpoint — several of them are described in "
               "detail in the sections above." % len(commits), "lead")])
        notes = {
            "Keep the LinkedIn panel current, and the drafts ready to paste":
                "Added the standalone Refresh LinkedIn button with its own "
                "in-flight flag, and the recommended comment drafts with the "
                "substance check in front of them.",
            "Move the company profile above the opportunities panel on Home":
                "The company panel argues for the pitch below it, so it has "
                "to be read first.",
            "Attribute LinkedIn activity by evidence, not just the CSV slug":
                "Rules 2 and 3 of section 8, plus "
                "<font face=\"Courier\">linkedin_observed</font> and the "
                "drift warning. Reverted the “unverified” badge "
                "experiment.",
            "Segment the list by country and category":
                "Both filters derived on read — section 12.",
            "PeopleIntel: lead research dashboard with outreach sequencing":
                "The initial build: the nine steps, the six tables, the two "
                "statuses, the importer's tolerances, corroborated web "
                "research, and the outreach sequence.",
        }
        rows = []
        for sha, when, subject in commits:
            note = notes.get(subject.strip(), "")
            body = "<b>%s</b>" % subject
            if note:
                body += "<br/><font color=\"#4B5563\" size=\"8\">%s</font>" % note
            rows.append([
                Paragraph("<font face=\"Courier\">%s</font>" % sha, ST["cell"]),
                Paragraph(when, ST["cell"]),
                Paragraph(body, ST["cell"]),
            ])
        # Both narrow columns hold a fixed-width string that must not wrap: a
        # seven-character sha in Courier, and an ISO date.
        add(Grid(["Commit", "Date", "What changed"], rows,
                 [21 * mm, 22 * mm, COL_W - 43 * mm]))
    else:
        add([P("Git history unavailable.", "note")])

    add([P("A pattern worth naming", "h2")])
    add([P("Three separate times, a looser rule was built, measured against "
           "the real data, found to be wrong, and removed: name-token matching "
           "for LinkedIn activity, the “identity unconfirmed” badge "
           "on web findings, and the merged-blob search in the opportunities "
           "matcher. In each case the replacement stores less and says more "
           "about what it does store. The measurements are quoted in sections "
           "8, 9 and 11 rather than summarised away, because they are the "
           "argument for the current rule.")])

    # --------------------------------------------------------- 16. limits
    add(H1("Limits and what is not built", "Section 16"))
    add([P("Stated here rather than discovered later.", "lead")])
    add(Grid(
        ["Not built", "Why, and what it would take"],
        [["<b>Email generation</b>",
          "Deliberately out of scope. The schema has room for it, and step 4 "
          "of the outreach sequence already tracks the email a person sends."],
         ["<b>Direct phone numbers</b>",
          "The Apollo export has none for any of the 25 contacts — only "
          "company switchboards. Direct lines need a paid waterfall "
          "enrichment, so the field is shown as an optional gap and never "
          "holds a contact at Needs Enrichment."],
         ["<b>Email deliverability</b>",
          "<font face=\"Courier\">resolve.py</font> claims only that an "
          "address is <i>published as theirs</i> — which is why what it "
          "writes lands in <font face=\"Courier\">email_status</font> as "
          "“found on the web” rather than “valid”. Proving "
          "a mailbox exists needs a verification provider and another key."],
         ["<b>Person photos</b>",
          "Not in the CSV, and LinkedIn's images cannot be hotlinked or "
          "reused. The avatar is initials; company logos come from favicons."],
         ["<b>Authentication</b>",
          "The header shows a fixed user. Real accounts are not implemented, "
          "so this is a tool for one team on a trusted network."],
         ["<b>Refresh scheduling</b>",
          "Research is fetched once. The LinkedIn panel can now be refreshed "
          "on demand, but nothing re-runs on a schedule — so a “2 "
          "days ago” activity will read “9 days ago” a week "
          "later, and web findings drift out of date."],
         ["<b>Serverless deployment</b>",
          "SQLite needs a persistent disk. On Vercel and similar the "
          "filesystem is ephemeral and writes vanish between requests. Point "
          "<font face=\"Courier\">app/db.py</font> at Neon or Supabase and "
          "nothing else changes."]],
        [38 * mm, COL_W - 38 * mm]))

    add([P("And the limits that are features", "h2")])
    add(Bullets([
        "A contact whose posts a search engine has never indexed gets an empty "
        "LinkedIn panel, and it is not padded.",
        "A contact with thin evidence gets no interest chips and no focus "
        "line.",
        "A relevant page that never repeats the person's title and employer in "
        "its snippet is dropped along with the namesakes.",
        "A capability with nothing to point at is left off the opportunities "
        "panel.",
        "Each of those is a deliberate trade of recall for precision, made "
        "because the output is read by a person who is about to contact a "
        "stranger — and a wrong row costs more there than a missing one.",
    ]))
    return b


def current_branch():
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                             cwd=ROOT, capture_output=True, text=True,
                             timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ------------------------------------------------------------------- canvas
def cover_bg(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(PLUM)
    canvas.rect(0, PAGE_H - 12 * mm, PAGE_W, 12 * mm, stroke=0, fill=1)
    canvas.setFillColor(VIOLET)
    canvas.rect(0, PAGE_H - 12 * mm, PAGE_W * 0.34, 12 * mm, stroke=0, fill=1)
    canvas.setFillColor(SHADE)
    canvas.rect(0, 0, PAGE_W, 6 * mm, stroke=0, fill=1)
    canvas.restoreState()


def page_furniture(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(PLUM)
    canvas.rect(0, PAGE_H - 4 * mm, PAGE_W, 4 * mm, stroke=0, fill=1)
    canvas.setFont(BODY, 7.6)
    canvas.setFillColor(FAINT)
    canvas.drawString(MARGIN, 11 * mm, "PeopleIntel · Project Guide")
    canvas.drawRightString(PAGE_W - MARGIN, 11 * mm, str(doc.page))
    canvas.setStrokeColor(RULE_C)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 14.5 * mm, PAGE_W - MARGIN, 14.5 * mm)
    canvas.restoreState()


def build():
    facts = db_facts()
    doc = BaseDocTemplate(
        OUT, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        # ASCII only: reportlab writes metadata in PDFDocEncoding, where
        # an em dash comes back as a replacement character in the viewer.
        title="PeopleIntel - Project Guide",
        author="PeopleIntel",
        subject="What has been built: architecture, rules and current state",
    )
    frame_cover = Frame(MARGIN, MARGIN, COL_W, PAGE_H - 2 * MARGIN,
                        id="cover", showBoundary=0)
    frame_body = Frame(MARGIN, 18 * mm, COL_W, PAGE_H - 18 * mm - 14 * mm,
                       id="body", showBoundary=0)
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame_cover], onPage=cover_bg),
        PageTemplate(id="body", frames=[frame_body], onPage=page_furniture),
    ])
    doc.build(blocks(facts, git_log(), line_counts()))
    print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    build()
