"""
Research one contact: company, web, LinkedIn links, interests.

Extracted so the CLI (run_pipeline.py) and the Research button in the UI run
exactly the same steps. Two copies of this would drift, and the one used less
often would be the one that rotted.

Nothing here prints. The caller decides how to report — the CLI writes lines,
the web request updates a status the page can show.
"""
import threading
import time
from datetime import datetime, timezone

from . import apify
from . import interests as interests_mod
from . import research
from . import resolve
from . import messages as messages_mod
from .db import SessionLocal
from .models import (
    CompanyFinding, Interest, LinkedInActivity, Person,
    RESEARCH_RUNNING,
)

MAX_ACTIVITIES = 5

# Seconds to wait between contacts in a queued run. The failures already on
# this database are Firecrawl 429s — requests per minute, with credits still
# on the plan — so the gap is the difference between a batch that finishes and
# one that fails halfway. Overridable for a plan with a higher ceiling.
QUEUE_GAP_SECONDS = 6

# One queue at a time, process-wide. Two batches interleaved would double the
# request rate and put us straight back into the 429s this gap exists to
# avoid, and the reader has no way to tell two batches apart anyway.
_queue_lock = threading.Lock()
_queue_state = {"running": False, "done": 0, "total": 0, "current": None,
                "failed": 0, "stopped": False, "kind": "research"}


def _fail(db, person, kind, exc):
    """Record a failure against whichever job was running.

    A LinkedIn refresh must not mark the contact's RESEARCH failed: the
    research is fine, and doing so would offer a full re-run to fix a panel.
    """
    if kind == "linkedin":
        person.linkedin_refreshing = False
        person.linkedin_refresh_error = str(exc)[:200]
    else:
        person.recompute_status(research_failed=True, note=str(exc)[:200])
    db.commit()


def queue_status():
    """A snapshot of the batch, for the page to report. Never blocks."""
    return dict(_queue_state)


def stop_queue():
    """Ask the batch to stop after the contact it is on."""
    _queue_state["stopped"] = True


def queue_research(slugs, gap=None, kind="research"):
    """Work through each slug in turn, in one background thread. Returns at once.

    `kind` is "research" for the full pipeline or "linkedin" for the activity
    search alone. The second exists because a change to what the search asks
    for does nothing to rows already stored, and re-running everything to
    refresh one panel spends far more than it needs to.

    Serial on purpose — see QUEUE_GAP_SECONDS. Each contact is marked running
    before its turn and resolved after it, so the existing badge and the
    auto-refresh report progress with nothing new to render.

    Returns the number queued, or 0 if a batch is already in flight.
    """
    slugs = [s for s in dict.fromkeys(slugs) if s]        # de-dupe, keep order
    if not slugs:
        return 0
    if not _queue_lock.acquire(blocking=False):
        return 0

    _queue_state.update(running=True, done=0, total=len(slugs), current=None,
                        failed=0, stopped=False, kind=kind)

    wait = QUEUE_GAP_SECONDS if gap is None else gap

    def run():
        try:
            for i, slug in enumerate(slugs):
                if _queue_state["stopped"]:
                    break
                db = SessionLocal()
                try:
                    person = db.query(Person).filter(
                        Person.slug == slug).first()
                    if person is None:
                        continue
                    _queue_state["current"] = slug
                    if kind == "linkedin":
                        # No mark_running: this does not touch research_status,
                        # and parking the contact at "Researching" would offer
                        # to start a full run the moment the badge cleared.
                        person.linkedin_refreshing = True
                        db.commit()
                    else:
                        mark_running(db, person)
                    try:
                        if kind == "linkedin":
                            refresh_linkedin(db, person)
                            person.linkedin_refreshing = False
                            person.linkedin_refresh_error = None
                            db.commit()
                        else:
                            research_contact(db, person)
                    except Blocked as e:
                        # The whole run cannot proceed - no key, bad key, no
                        # credits. Carrying on would mark every remaining
                        # contact failed for a reason that is not about them.
                        _fail(db, person, kind, e)
                        _queue_state["failed"] += 1
                        break
                    except Exception as e:
                        _fail(db, person, kind, e)
                        _queue_state["failed"] += 1
                finally:
                    db.close()
                _queue_state["done"] = i + 1
                if i + 1 < len(slugs) and not _queue_state["stopped"]:
                    time.sleep(wait)
        finally:
            _queue_state.update(running=False, current=None)
            _queue_lock.release()

    threading.Thread(target=run, name="research-queue", daemon=True).start()
    return len(slugs)


class Blocked(RuntimeError):
    """The whole run can't proceed: no key, bad key, or no credits.

    Distinct from a per-step failure. One contact's search failing is that
    contact's problem; this is everybody's, so the caller should stop rather
    than mark 25 people as failed in turn.
    """


def refresh_linkedin(db, person):
    """Re-run just the LinkedIn Activity search for one contact.

    Extracted out of research_contact so the full pipeline and the standalone
    Refresh button run exactly this, and only this, step — the module
    docstring's reason for research_contact existing at all applies just as
    much to a second copy of its LinkedIn block.

    Only search-found rows are ever touched; anything pasted by hand is never
    replaced (see the `pasted` filter below). A previous search run's rows are
    replaced only when this one actually turns something up, so a transient
    empty result can't wipe out what's already shown — the same guard
    research_contact's own LinkedIn step already relied on.

    Returns the number of activities found this run. Raises Blocked when the
    API refuses the run outright (no key, bad key, no credits).
    """
    pasted = [a for a in person.activities if not a.from_search]
    room = MAX_ACTIVITIES - len(pasted)
    if room <= 0:
        return 0

    # Apify reads the contact's own profile, so it is the source whenever a
    # token is configured: the post body and its exact date come back as
    # data, and authorship is not in question. Firecrawl is not asked about
    # LinkedIn at all in that case — it cannot fetch the site, and a name
    # search returns other people's posts. It keeps the company and web work.
    #
    # Without a token the Firecrawl path still runs, so an install that has
    # not set one up is no worse off than before.
    observed = None
    if apify.configured():
        url = apify.profile_url(person)
        try:
            found, note = apify.fetch_posts(url, limit=room)
        except apify.ApifyRejected as exc:
            person.linkedin_posts_checked_at = datetime.now(timezone.utc)
            person.linkedin_posts_source = "apify"
            person.linkedin_posts_note = str(exc)[:300]
            db.commit()
            raise Blocked(str(exc)) from exc
        person.linkedin_posts_source = "apify"
        person.linkedin_posts_note = note or None
    else:
        found, observed = research.find_linkedin_activity(person, limit=room)
        person.linkedin_posts_source = "firecrawl"
        person.linkedin_posts_note = (
            None if found else
            "No posts found through web search. Firecrawl cannot read "
            "LinkedIn directly — set an Apify token on the Settings page to "
            "read the profile itself."
        )
    person.linkedin_posts_checked_at = datetime.now(timezone.utc)
    db.commit()

    if observed:
        person.linkedin_observed = observed["url"]
        person.linkedin_observed_source = observed["evidence"][:600]
        person.linkedin_observed_at = observed["fetched_at"]
        db.commit()
    known = {research.clean_activity_url(a.url) for a in pasted}
    found = [f for f in found if f["url"] not in known]
    if found:
        for old_row in [a for a in person.activities if a.from_search]:
            db.delete(old_row)
        db.flush()
        for i, row in enumerate(found, start=len(pasted) + 1):
            db.add(LinkedInActivity(person=person, **dict(row, rank=i)))
        db.commit()
    return len(found)


def research_contact(db, person, skip_interests=False, skip_linkedin=False,
                     skip_resolve=False, force=False):
    """Run every research step for one contact and commit as it goes.

    Returns a dict of counts and a list of non-fatal step failures. Raises
    Blocked when the API refuses the whole run.
    """
    out = {"activities": 0, "interests": 0,
           "comments_drafted": 0, "company_described": False,
           "company_findings": 0,
           "resolved": [], "problems": []}

    # ---- step 3: missing data enrichment, first
    #
    # Before everything else, because the rest of the pipeline reads these two
    # fields: the LinkedIn slug is what attributes activity to this contact
    # rather than a namesake, so filling it here means step 4 can actually use
    # it on the same run instead of the next one.
    #
    # Only ever fills a blank. A value that came from the CSV is left alone —
    # the file is the source for what it contains, and overwriting it with a
    # web guess would lose the better fact.
    if not skip_resolve and (not person.email or not person.linkedin_url):
        try:
            report = resolve.resolve_identity(db, person)
            out["resolved"] = report["filled"]
        except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
            raise Blocked(str(e)) from e
        except Exception as e:
            out["problems"].append(f"identity enrichment: {e}")

    # ---- step 7: company research (once per company)
    if person.company and not person.company.description:
        try:
            found = research.research_company(person.company)
            if found:
                person.company.description = found["description"]
                person.company.description_source_url = found["source_url"]
                person.company.description_fetched_at = found["fetched_at"]
                db.commit()
                out["company_described"] = True
        except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
            raise Blocked(str(e)) from e
        except Exception as e:
            out["problems"].append(f"company research: {e}")

    # ---- step 7b: what the web says about the company
    #
    # Once per company, not once per contact: the answer is the same for
    # everyone who works there, so the second contact at the same employer
    # costs nothing. `web_checked_at` is what makes that decidable, and it is
    # set whether or not anything was found — an empty result is an answer, and
    # re-asking it every run would spend credits to learn the same nothing.
    if person.company and (force or not person.company.web_checked_at):
        try:
            result = research.research_company_web(person.company)
            if force:
                for old_row in list(person.company.findings):
                    person.company.findings.remove(old_row)
                db.flush()
            for row in result["rows"]:
                db.add(CompanyFinding(company=person.company, **row))
            person.company.web_checked_at = datetime.now(timezone.utc)
            person.company.web_note = result["note"]
            out["company_findings"] = len(result["rows"])
            db.commit()
        except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
            raise Blocked(str(e)) from e
        except Exception as e:
            out["problems"].append(f"company web research: {e}")

    # ---- step 5: web research about the person — REMOVED
    #
    # It cost two Firecrawl searches per contact and had no panel: nothing in
    # the app displayed a WebFinding, only counted them. The company question
    # is asked properly by research_company_web above, and what the person
    # themselves said is LinkedIn activity, which step 4 collects. The model,
    # the table and the rows already gathered are left alone — interest
    # detection still reads them where they exist.

    # ---- step 4: LinkedIn activity links
    # Before interest detection, because chips are derived from the text of
    # these, so they have to exist first.
    if not skip_linkedin:
        try:
            out["activities"] = refresh_linkedin(db, person)
        except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
            raise Blocked(str(e)) from e
        except Exception as e:
            out["problems"].append(f"LinkedIn activity: {e}")

    # ---- step 6: interest detection
    if not skip_interests:
        try:
            detected = interests_mod.detect(person)
            if detected["interests"]:
                for old in list(person.interests):
                    db.delete(old)
                db.flush()
                for item in detected["interests"]:
                    db.add(Interest(person=person, **item))
                out["interests"] = len(detected["interests"])
            if detected.get("focus"):
                person.focus_line = detected["focus"]
                person.focus_generated_at = datetime.now(timezone.utc)
                person.focus_model = detected.get("model")
            db.commit()
        except interests_mod.LLMNotConfigured as e:
            out["problems"].append(f"interest detection skipped: {e}")
        except Exception as e:
            out["problems"].append(f"interest detection: {e}")

        # ---- recommended comment per post
        # After interest detection, because the draft is given the interest
        # chips as context — the same reason activities come before interests.
        # Uses the same LLM endpoint, so it is gated on the same flag.
        try:
            counts = messages_mod.draft_for_person(db, person)
            out["comments_drafted"] = counts["drafted"]
        except messages_mod.LLMNotConfigured as e:
            out["problems"].append(f"comment drafting skipped: {e}")
        except Exception as e:
            out["problems"].append(f"comment drafting: {e}")

    # Stamped before recompute_status, which reads it through is_researched.
    person.research_completed_at = datetime.now(timezone.utc)
    person.recompute_status()
    db.commit()
    return out


def research_in_background(slug, skip_interests=False, skip_linkedin=False,
                           skip_resolve=False, force=False):
    """Start a research run for one contact and return immediately.

    Research takes tens of seconds — longer when the API rate-limits and the
    backoff waits — so holding an HTTP request open for it would look like a
    hung page. The contact is parked at "Researching" first, so a refresh shows
    what's happening, and the thread opens its own session because the
    request's is closed the moment the response goes out.
    """
    def run():
        db = SessionLocal()
        try:
            person = db.query(Person).filter(Person.slug == slug).first()
            if person is None:
                return
            try:
                research_contact(db, person, skip_interests=skip_interests,
                                 skip_linkedin=skip_linkedin,
                                 skip_resolve=skip_resolve, force=force)
            except Blocked as e:
                person.recompute_status(research_failed=True, note=str(e)[:200])
                db.commit()
            except Exception as e:                     # never leave it stuck
                person.recompute_status(research_failed=True, note=str(e)[:200])
                db.commit()
        finally:
            db.close()

    thread = threading.Thread(target=run, name=f"research:{slug}", daemon=True)
    thread.start()
    return thread


def mark_running(db, person):
    """Park the contact at Researching so the page can say so."""
    person.research_status = RESEARCH_RUNNING
    person.status_note = None
    db.commit()


def clear_orphaned_runs(db):
    """Reset work left in flight by a killed process — research and refresh.

    Both run in daemon threads, so neither can outlive the process that
    started it: a restart, a crash or a --reload mid-run leaves the flag set
    with nothing still working on it. The page would then show "Researching…"
    or "Refreshing…" for good and never offer the button again, with no way
    back from the UI. Anything still marked in flight at startup is by
    definition an orphan.

    Returns {"research": n, "linkedin": n}.
    """
    # Research runs in a daemon thread for exactly the same reason and strands
    # exactly the same way — worse, in fact: can_research is False while a
    # contact is "running", so the button disappears and there is no way back
    # from the UI at all. run.ps1 passes --reload by default, so saving a file
    # mid-run is enough to cause it.
    orphaned = db.query(Person).filter(
        Person.research_status == RESEARCH_RUNNING).all()
    for person in orphaned:
        person.recompute_status(
            research_failed=True,
            note="Interrupted - the server restarted while this research was "
                 "running. Nothing was saved from it; press Retry.")

    stuck = db.query(Person).filter(Person.linkedin_refreshing == True).all()  # noqa: E712
    for person in stuck:
        person.linkedin_refreshing = False
        person.linkedin_refresh_error = (
            "Interrupted — the server restarted while this refresh was running."
        )
    # Commit if EITHER sweep found something. Gating this on `stuck` alone
    # meant the research reset was rolled back the moment nothing needed a
    # LinkedIn reset — which is most of the time.
    if orphaned or stuck:
        db.commit()
    return {"research": len(orphaned), "linkedin": len(stuck)}


def refresh_linkedin_in_background(slug):
    """Start a standalone LinkedIn refresh for one contact and return.

    Mirrors research_in_background, but tracks its own flag
    (linkedin_refreshing) rather than research_status — this is a re-check of
    one panel, not a re-run of the whole record, so it shouldn't flip the
    contact back to "Researching…" or block the (separate) Research button.
    """
    def run():
        db = SessionLocal()
        try:
            person = db.query(Person).filter(Person.slug == slug).first()
            if person is None:
                return
            try:
                refresh_linkedin(db, person)
                person.linkedin_refresh_error = None
            except Exception as e:                     # never leave it stuck
                # research.FirecrawlNotConfigured/FirecrawlRejected included:
                # refresh_linkedin doesn't wrap them in Blocked the way
                # research_contact's caller does, since this run has no
                # further steps for a Blocked/Exception split to matter to.
                person.linkedin_refresh_error = str(e)[:200]
            finally:
                person.linkedin_refreshing = False
                person.linkedin_refreshed_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()

    thread = threading.Thread(target=run, name=f"linkedin-refresh:{slug}",
                              daemon=True)
    thread.start()
    return thread
