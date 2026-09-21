"""
What the app knows about the person using it, and how it learns.

A model call carries nothing over from the last one. There is no server-side
memory to write to, so the memory is here: rows in our own database, updated as
you work, rendered into the system prompt on every draft. The model is handed a
fresh memory each time and holds none.

Four categories, kept apart because they are not equally trustworthy:

  fact        who you are. Seeded from OUTREACH_SENDER_*, extended by you.
  preference  something you stated. True on arrival; an inference never
              overrules it and the app never retires it.
  behaviour   something worked out from your edits. Must be seen twice before
              it is injected — see the confirmation floor below.
  history     one rolling summary of everything so far. Replaces itself.

The raw feedback log is kept in full and **never injected**. Only the history
summary goes into a prompt. That split is the whole reason this stays affordable
as the log grows.

The confirmation floor is the local version of a rule this codebase already
applies twice: an empty answer beats a plausible one. interests.py refuses to
infer from fewer than two pieces of evidence; so does this. One edit proposes,
two edits confirm.
"""
import os
import re
from datetime import datetime, timezone

from . import llm
from .models import (DraftFeedback, ProfileEntry, PROFILE_CATEGORIES,
                     PROFILE_SCOPES)

# Injected into a prompt that is already ~13,600 characters. Roughly a tenth on
# top, which is enough for a dozen entries and small enough that nobody has to
# think about it. There is no token accounting anywhere in this app, so this
# ceiling is the only thing standing between memory and an unbounded prompt.
PROFILE_CHARS = 1500

# Seen once, it is a one-off. Seen twice, it is how you write.
CONFIRM_AT = 2

# Per scope, so a long tail of near-duplicates cannot crowd out the good ones.
CAPS = {"global": 8, "vertical": 6, "person": 4}

# How often the log is compressed into one paragraph.
SUMMARISE_EVERY = 10

OFF_VAR = "FEEDBACK_OFF"


def enabled():
    """Whether to ask about drafts at all."""
    return (os.environ.get(OFF_VAR) or "").strip().lower() not in ("1", "true", "yes")


def _now():
    return datetime.now(timezone.utc)


def _norm(text):
    """For duplicate detection. Case, punctuation and spacing are not the
    difference between two rules; the words are."""
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


def _same_rule(a, b):
    """Two entries saying the same thing.

    Deliberately blunt — a word-overlap ratio, not a model call. Getting this
    slightly wrong merges two near-identical rules or keeps both, and neither
    is worth an API request to avoid.
    """
    wa, wb = set(_norm(a)), set(_norm(b))
    if not wa or not wb:
        return False
    return len(wa & wb) / max(len(wa), len(wb)) >= 0.7


# ------------------------------------------------------------- the facts ----
def seed_facts(db):
    """Make sure who-you-are is on file, from the environment.

    Idempotent, and it never overwrites: if you have edited your role on the
    Profile page, the env var does not get to put it back.
    """
    from .messages import sender
    who = sender()
    wanted = {
        "name": f"You are {who['name']}.",
        "role": f"Your role is {who['role']} at {who['company']}.",
    }
    existing = {e.key for e in db.query(ProfileEntry)
                .filter(ProfileEntry.category == "fact").all() if e.key}
    added = []
    for key, text in wanted.items():
        if key in existing:
            continue
        db.add(ProfileEntry(category="fact", key=key, text=text, source="told",
                            evidence="from the app's sender settings",
                            scope="global", applies_to="both", active=True,
                            times_seen=1))
        added.append(key)
    if added:
        db.commit()
    return added


def add_told(db, category, text, key=None, applies_to="both"):
    """Something you typed. Active immediately — you are the authority on it.

    A keyed fact replaces the previous answer to the same question rather than
    stacking a second one.
    """
    text = (text or "").strip()
    if not text:
        return None
    if category not in PROFILE_CATEGORIES:
        category = "preference"

    if key:
        prior = (db.query(ProfileEntry)
                 .filter(ProfileEntry.category == category,
                         ProfileEntry.key == key).first())
        if prior:
            prior.text, prior.updated_at = text, _now()
            prior.active = True
            db.commit()
            return prior

    entry = ProfileEntry(category=category, key=key, text=text, source="told",
                         evidence="you told it", scope="global",
                         applies_to=applies_to, active=True, times_seen=1)
    db.add(entry)
    db.commit()
    return entry


# ----------------------------------------------- signals that need no model --
def measured_signals(feedback):
    """What changed, measured rather than guessed.

    These cost nothing, cannot hallucinate, and are the most reliable thing in
    the whole layer — so they are computed before any model is asked, and they
    stand on their own if the call fails.

    Returns a list of dicts shaped like the model's own output.
    """
    out = []
    draft, final = feedback.draft_body or "", feedback.final_body or ""
    if not (draft and final):
        return out

    dw, fw = len(draft.split()), len(final.split())
    if dw and abs(fw - dw) / dw >= 0.2:
        shorter = fw < dw
        out.append({
            "text": (f"You cut drafts down — this one went from {dw} to {fw} "
                     f"words." if shorter else
                     f"You add to drafts — this one went from {dw} to {fw} "
                     f"words."),
            "evidence": f"{dw} words drafted, {fw} words sent",
            "category": "behaviour", "scope": "global",
            "applies_to": feedback.kind, "mechanical": False, "pattern": None,
        })

    if "?" in draft and "?" not in final:
        out.append({
            "text": "You take the question out of the closing line.",
            "evidence": "the draft ended on a question; what you sent did not",
            "category": "behaviour", "scope": "global",
            "applies_to": feedback.kind, "mechanical": False, "pattern": None,
        })

    d_first = _first_sentence(draft)
    f_first = _first_sentence(final)
    if d_first and f_first and not _same_rule(d_first, f_first):
        out.append({
            "text": "You rewrite the opening line rather than keeping it.",
            "evidence": f"drafted {d_first!r}, sent {f_first!r}",
            "category": "behaviour", "scope": "global",
            "applies_to": feedback.kind, "mechanical": False, "pattern": None,
        })
    return out


def _first_sentence(text):
    for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        clean = part.strip()
        # Skip the greeting — "Hi Laura," is not the opening line anyone means.
        if clean and not re.match(r"^(hi|hello|dear)\b", clean, re.I):
            return clean[:160]
    return ""


# ----------------------------------------------------------- distillation ---
SYSTEM = """You work out how one person writes, from how they edited a draft.

Rules:
1. Every observation must quote what changed. No quote, no observation.
2. An edit that shows nothing general is not an observation. Returning an empty
   list is the correct answer for a one-off, and is expected most of the time.
3. Describe what they DO, not what the draft did wrong. "You open on the
   number, not the name" — not "the draft was too vague".
4. Mark an observation mechanical only when it is a literal phrase that could
   be searched for. Anything needing judgement is not mechanical.
5. Never infer anything about the recipient's health, politics, religion or
   personal life, and never anything about the sender beyond how they write.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidence": {"type": "string"},
                    "category": {"type": "string",
                                 "enum": ["preference", "behaviour"]},
                    "scope": {"type": "string",
                              "enum": ["global", "vertical", "person"]},
                    "applies_to": {"type": "string",
                                   "enum": ["email", "comment", "both"]},
                    "mechanical": {"type": "boolean"},
                    "pattern": {"type": ["string", "null"]},
                },
                "required": ["text", "evidence", "category", "scope",
                             "applies_to", "mechanical", "pattern"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["observations"],
    "additionalProperties": False,
}


def _context(db, feedback, limit=10):
    """This edit, and the recent ones, so a pattern can be told from a one-off."""
    lines = [
        f"Kind: {feedback.kind}",
        f"Outcome: {feedback.outcome}",
    ]
    if feedback.vertical:
        lines.append(f"Their vertical: {feedback.vertical}")
    if feedback.reason:
        lines.append(f"Why they changed it, in their words: {feedback.reason}")
    lines += ["", "The draft:", feedback.draft_body or "(none)"]
    if feedback.final_body:
        lines += ["", "What they actually sent:", feedback.final_body]

    prior = (db.query(DraftFeedback)
             .filter(DraftFeedback.kind == feedback.kind,
                     DraftFeedback.id != feedback.id,
                     DraftFeedback.outcome.in_(("edited", "unused")))
             .order_by(DraftFeedback.created_at.desc()).limit(limit).all())
    if prior:
        lines += ["", "Earlier edits, for deciding whether this is a pattern "
                      "or a one-off:"]
        for row in prior:
            note = (row.reason or "").strip()
            lines.append(f"- {row.outcome}"
                         + (f", because: {note}" if note else "")
                         + (f" ({row.word_delta:+d} words)"
                            if row.word_delta is not None else ""))
    return "\n".join(lines)


def observe(db, feedback):
    """Learn from one answer. Returns the entries created or strengthened.

    Never raises: a failure to learn must not cost the user the feedback they
    just gave, which is already committed by the time this runs.
    """
    if feedback.outcome in ("used", "skipped"):
        return []

    proposals = list(measured_signals(feedback))

    try:
        parsed, model = llm.json_call(SYSTEM, _context(db, feedback),
                                      schema=SCHEMA, max_tokens=1200)
        for item in (parsed or {}).get("observations") or []:
            proposals.append(item)
    except llm.LLMNotConfigured:
        model = None                      # measured signals still stand
    except Exception:
        model = None

    touched = []
    for item in proposals:
        entry = _merge(db, feedback, item, model)
        if entry is not None:
            touched.append(entry)

    if touched:
        db.commit()
        for scope in set(e.scope for e in touched):
            _evict(db, scope)
        db.commit()

    _maybe_summarise(db)
    return touched


def _merge(db, feedback, item, model):
    """Fold one observation into the profile, or drop it."""
    text = (item.get("text") or "").strip()
    evidence = (item.get("evidence") or "").strip()
    # Rule 1, enforced here and not merely requested in the prompt — the same
    # check interests.py makes on a chip.
    if not text or not evidence:
        return None

    category = item.get("category") or "behaviour"
    if category not in ("preference", "behaviour"):
        category = "behaviour"
    scope = item.get("scope") or "global"
    if scope not in PROFILE_SCOPES:
        scope = "global"

    scope_key = None
    if scope == "vertical":
        scope_key = feedback.vertical
        if not scope_key:
            scope = "global"              # no vertical on file, so no bucket
    elif scope == "person":
        scope_key = str(feedback.person_id)

    for prior in (db.query(ProfileEntry)
                  .filter(ProfileEntry.scope == scope,
                          ProfileEntry.category == category).all()):
        if prior.scope_key == scope_key and _same_rule(prior.text, text):
            prior.times_seen = (prior.times_seen or 1) + 1
            prior.evidence = evidence
            prior.updated_at = _now()
            if prior.times_seen >= CONFIRM_AT:
                prior.active = True
            return prior

    entry = ProfileEntry(
        category=category, text=text, source="inferred", evidence=evidence,
        scope=scope, scope_key=scope_key,
        applies_to=item.get("applies_to") or "both",
        mechanical=bool(item.get("mechanical")),
        pattern=(item.get("pattern") or None),
        times_seen=1,
        # Seen once. Stored so a second sighting can confirm it, inactive so it
        # cannot shape a draft on the strength of one edit.
        active=False,
        model=model,
    )
    db.add(entry)
    return entry


def _age(entry):
    """A sortable timestamp, whatever the row's provenance.

    SQLite hands back naive datetimes, while a row created a moment ago in this
    session still carries the tz-aware value the default produced. Sorting a
    mix of the two raises, which is exactly the state eviction runs in — some
    rows loaded, some just written.
    """
    stamp = entry.updated_at or entry.created_at
    if stamp is None:
        return datetime.min
    return stamp.replace(tzinfo=None)


def _evict(db, scope):
    """Hold each scope to its cap, weakest first. Never touches a stated entry."""
    cap = CAPS.get(scope, 8)
    rows = [e for e in db.query(ProfileEntry)
            .filter(ProfileEntry.scope == scope).all() if not e.locked]
    by_key = {}
    for row in rows:
        by_key.setdefault(row.scope_key, []).append(row)
    for group in by_key.values():
        if len(group) <= cap:
            continue
        group.sort(key=lambda e: ((e.times_seen or 1), _age(e)))
        for doomed in group[:len(group) - cap]:
            db.delete(doomed)


# --------------------------------------------------------------- history ----
SUMMARY_SYSTEM = """You compress a log of edits into one short paragraph.

Say what the person consistently does to drafts. Three sentences at most. No
preamble, no bullet list, no advice — just what the record shows. If the log is
too thin to say anything, return an empty string.
"""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


def _maybe_summarise(db):
    """Roll the log up every SUMMARISE_EVERY answers.

    Replaces the previous summary rather than adding to it. That is the
    difference between a memory that stays one paragraph and a prompt that
    grows with every message ever sent.
    """
    total = db.query(DraftFeedback).filter(
        DraftFeedback.outcome.in_(("edited", "unused", "used"))).count()
    if not total:
        return None

    prior = (db.query(ProfileEntry)
             .filter(ProfileEntry.category == "history").first())
    # How many answers the standing summary was built from. Compared as a
    # watermark rather than testing `total % SUMMARISE_EVERY`: a modulo fires
    # only on an exact multiple, so one answer deleted — or two arriving
    # between calls — and the summary would never be written again.
    since = total - (prior.times_seen or 0 if prior else 0)
    if since < SUMMARISE_EVERY:
        return None

    rows = (db.query(DraftFeedback).order_by(DraftFeedback.created_at.desc())
            .limit(40).all())
    lines = []
    for row in rows:
        bit = f"- {row.kind}: {row.outcome}"
        if row.word_delta is not None:
            bit += f" ({row.word_delta:+d} words)"
        if row.reason:
            bit += f" — {row.reason.strip()[:160]}"
        lines.append(bit)

    try:
        parsed, model = llm.json_call(SUMMARY_SYSTEM,
                                      f"{total} drafts so far.\n\n"
                                      + "\n".join(lines),
                                      schema=SUMMARY_SCHEMA, max_tokens=500)
    except Exception:
        return None

    summary = ((parsed or {}).get("summary") or "").strip()
    if not summary:
        return None

    if prior:
        prior.text, prior.updated_at, prior.model = summary, _now(), model
        prior.times_seen = total
        prior.active = True
    else:
        db.add(ProfileEntry(category="history", text=summary,
                            source="summarised",
                            evidence=f"the last {len(rows)} answers",
                            scope="global", applies_to="both", active=True,
                            times_seen=total, model=model))
    db.commit()
    return summary


# ------------------------------------------------------------ rendering -----
_ORDER = ("fact", "preference", "behaviour", "history")

_HEADINGS = {
    "fact": None,                                   # stated as plain sentences
    "preference": "Preferences they have stated:",
    "behaviour": "Patterns in how they edit drafts:",
    "history": None,
}


def entries_for(db, person=None, kind="email", include_inactive=False):
    """The entries that apply here, strongest scope first."""
    rows = db.query(ProfileEntry).all()
    vertical = _vertical_of(person)
    person_key = str(person.id) if person is not None and person.id else None

    keep = []
    for row in rows:
        if not include_inactive and not row.active:
            continue
        if row.applies_to not in ("both", kind):
            continue
        if row.scope == "vertical" and row.scope_key != vertical:
            continue
        if row.scope == "person" and row.scope_key != person_key:
            continue
        keep.append(row)

    keep.sort(key=lambda e: (_ORDER.index(e.category)
                             if e.category in _ORDER else 9,
                             0 if e.locked else 1,
                             -(e.times_seen or 1)))
    return keep


def _vertical_of(person):
    if person is None or not person.company:
        return None
    from .email_template import vertical_for
    try:
        key, _spec = vertical_for(person)
        return key
    except Exception:
        return None


def render(db, person=None, kind="email", budget=PROFILE_CHARS):
    """The block that goes into the system prompt, or "" when there is nothing.

    Truncated to a budget rather than trusted to stay small. Entries are
    already ordered so that what gets dropped is the weakest inference, never a
    stated preference.
    """
    rows = entries_for(db, person, kind)
    if not rows:
        return ""

    out, used, seen_heading = [], 0, set()
    for row in rows:
        heading = _HEADINGS.get(row.category)
        line = row.text.strip()
        if not line:
            continue
        prefix = ""
        if heading and heading not in seen_heading:
            prefix = heading + "\n"
        bullet = ("- " + line) if heading else line
        if row.category == "behaviour" and (row.times_seen or 1) > 1:
            bullet += f" (seen {row.times_seen}x)"
        chunk = prefix + bullet
        if used + len(chunk) > budget:
            break
        out.append(chunk)
        used += len(chunk) + 1
        if heading:
            seen_heading.add(heading)

    return "\n".join(out)


def applied_ids(db, person=None, kind="email"):
    """The ids behind whatever render() just produced, for the audit trail."""
    return ",".join(str(e.id) for e in entries_for(db, person, kind))


def banned_patterns(db, kind="email"):
    """Literal phrases an active mechanical entry says to avoid.

    Returned so the existing validators can enforce them. A rule the prompt
    merely asks for is a rule that holds most of the time; this is the same
    principle the rest of the app already applies to drafted copy.
    """
    out = []
    for row in db.query(ProfileEntry).filter(
            ProfileEntry.mechanical == True).all():          # noqa: E712
        if not row.active or not row.pattern:
            continue
        if row.applies_to not in ("both", kind):
            continue
        out.append(row.pattern.strip().lower())
    return sorted(set(p for p in out if len(p) >= 4))
