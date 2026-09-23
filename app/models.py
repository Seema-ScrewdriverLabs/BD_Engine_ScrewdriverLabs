"""
PeopleIntel data model.

Design rule carried over from the v1 plan: every fact that was fetched from the
web stores WHERE it came from and WHEN. Fields that come straight from the
uploaded CSV don't need provenance (the file is the source); anything we went
and found ourselves does.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column, Integer, String, Text, DateTime, ForeignKey, Date, Float,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


def _title_key(t):
    """Loose comparison so 'Director, Content' == 'director content'."""
    import re
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def _profile_key(url):
    """Just the slug, so http/https, www/ca. and a trailing slash don't differ."""
    u = (url or "").lower().rstrip("/")
    return u.split("/in/")[-1].split("/")[0].split("?")[0] if "/in/" in u else u


# ---------------------------------------------------------------- statuses
# Two independent questions, previously answered by one field:
#
#   enrichment — do we know who this person is? Identity fields only.
#   research   — have we gone and looked them up yet?
#
# Conflating them made "Needs Enrichment" mean "something, somewhere, is
# missing", which is true of almost every contact and so told you nothing. A
# fully identified person who hasn't been researched is not missing data about
# themselves; a person with no email is, whether or not research has run. Both
# combinations are now expressible: Needs Enrichment + Researched is a real
# state, and so is Complete + Not researched.
STATUS_COMPLETE = "complete"
STATUS_NEEDS_ENRICHMENT = "needs_enrichment"
STATUS_FAILED = "failed"

STATUS_LABELS = {
    STATUS_COMPLETE: "Complete",
    STATUS_NEEDS_ENRICHMENT: "Needs Enrichment",
    STATUS_FAILED: "Failed",
}

RESEARCH_DONE = "researched"
RESEARCH_PENDING = "not_researched"
RESEARCH_RUNNING = "running"
RESEARCH_FAILED = "failed"

RESEARCH_LABELS = {
    RESEARCH_DONE: "Researched",
    RESEARCH_PENDING: "Not researched",
    RESEARCH_RUNNING: "Researching…",
    RESEARCH_FAILED: "Research failed",
}

# LinkedIn activity types. A repost counts as activity in its own right: it is
# something the person chose to put in front of their network, and it is not a
# post of theirs — conflating the two would overstate what they actually wrote.
ACTIVITY_POST = "post"
ACTIVITY_REPOST = "repost"
ACTIVITY_COMMENT = "comment"
ACTIVITY_TAGGED = "tagged"

ACTIVITY_LABELS = {
    ACTIVITY_POST: "Post",
    ACTIVITY_REPOST: "Repost",
    ACTIVITY_COMMENT: "Comment",
    ACTIVITY_TAGGED: "Tagged",
}


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)

    # --- from the CSV
    name = Column(String, nullable=False, index=True)
    website = Column(String)
    linkedin_url = Column(String)
    industry = Column(String)
    employees = Column(Integer)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    country = Column(String)
    phone = Column(String)
    technologies = Column(Text)
    keywords = Column(Text)
    annual_revenue = Column(Float)
    total_funding = Column(Float)
    latest_funding = Column(String)
    sic_codes = Column(String)
    naics_codes = Column(String)
    apollo_account_id = Column(String)

    # --- researched: "what they do". Provenance required.
    description = Column(Text)
    description_source_url = Column(String)
    description_fetched_at = Column(DateTime)

    # --- web research about the company itself.
    #
    # web_checked_at is the whole point of storing anything here: without it,
    # an empty panel cannot tell "nobody has looked yet" from "we looked and
    # the web has nothing", and those are opposite answers. web_note carries
    # the second one's reason so the panel can say it.
    web_checked_at = Column(DateTime)
    web_note = Column(String)

    people = relationship("Person", back_populates="company")
    findings = relationship(
        "CompanyFinding",
        back_populates="company",
        cascade="all, delete-orphan",
        order_by="CompanyFinding.rank",
    )

    @property
    def web_searched(self):
        """Whether anyone has actually run the company web search."""
        return self.web_checked_at is not None

    @property
    def findings_by_source(self):
        """Web findings grouped by the site they came from.

        One section per source rather than a flat numbered list, because a
        source is the unit a reader judges: two pieces from the company's own
        newsroom are one kind of evidence and a trade-press write-up is
        another, and interleaving them by rank makes the reader re-establish
        which is which on every row.

        Groups keep the order their first result appeared in, so the ranking
        the search produced still decides what is read first — the company's
        own site sorts last there, since the description panel already quotes
        it and outside coverage is what this panel is for.
        """
        groups, order = {}, []
        for finding in self.findings:
            key = finding.source_domain or "unknown"
            if key not in groups:
                groups[key] = {
                    "domain": key,
                    "own_site": finding.on_own_site,
                    "confirmation": finding.confirmation,
                    "fetched_at": finding.fetched_at,
                    "items": [],
                }
                order.append(key)
            group = groups[key]
            group["items"].append(finding)
            # The freshest fetch in the group is what the section reports.
            if finding.fetched_at and (not group["fetched_at"]
                                       or finding.fetched_at > group["fetched_at"]):
                group["fetched_at"] = finding.fetched_at
        return [groups[k] for k in order]

    @property
    def location(self):
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ", ".join(parts) if parts else None

    @property
    def domain(self):
        if not self.website:
            return None
        return (
            self.website.replace("https://", "")
            .replace("http://", "")
            .replace("www.", "")
            .rstrip("/")
        )

    @property
    def clean_description(self):
        """`description` as prose, with scraped markup removed.

        The research step stores whatever the source page gave it, and a
        scraped page brings headings, image tags and link syntax with it. Both
        the company card and the profile summary read this instead of the raw
        column so neither has to render "# **Close the gap...**".
        """
        from app.summary import clean_description
        return clean_description(self.description)

    @property
    def keyword_list(self):
        if not self.keywords:
            return []
        return [k.strip() for k in self.keywords.split(",") if k.strip()][:12]


class Person(Base):
    __tablename__ = "people"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, index=True)

    # --- from the CSV
    first_name = Column(String)
    last_name = Column(String)
    title = Column(String)
    seniority = Column(String)
    department = Column(String)
    sub_department = Column(String)

    email = Column(String)
    email_status = Column(String)

    # --- step 3, Missing Data Enrichment. A value this app went and found
    #     carries where it came from and when, exactly like a web finding.
    #     A value from the CSV carries nothing: the file is the source.
    email_source = Column(String)          # why we believe it is theirs
    email_source_url = Column(String)      # the page it was published on
    email_found_at = Column(DateTime)
    linkedin_source = Column(String)
    linkedin_found_at = Column(DateTime)
    # Why a missing field is still missing, so the page can say so rather than
    # showing a blank the reader has to interpret.
    identity_note = Column(String)
    linkedin_url = Column(String)
    twitter_url = Column(String)
    facebook_url = Column(String)

    phone_direct = Column(String)   # 0/25 in the Apollo export — usually empty
    phone_corporate = Column(String)

    city = Column(String)
    state = Column(String)
    country = Column(String)

    apollo_contact_id = Column(String)

    company_id = Column(Integer, ForeignKey("companies.id"))
    company = relationship("Company", back_populates="people")

    # --- pipeline state, drives the badges and the stat tiles
    # Whether this person's own identity is complete. Set only by
    # recompute_status(); never assign it directly.
    enrichment_status = Column(String, default=STATUS_NEEDS_ENRICHMENT, index=True)
    # Whether research has run for them, and how it went.
    research_status = Column(String, default=RESEARCH_PENDING, index=True)
    # When a run last finished. Recorded because "did research run" and "did
    # research find anything" are different questions, and inferring the first
    # from the second left a contact with no public footprint permanently at
    # Not researched — offering the button again, to re-spend credits learning
    # the same nothing. See is_researched.
    research_completed_at = Column(DateTime)

    # Legacy single field. Kept in step with enrichment_status so an older
    # query or export doesn't silently read a frozen value; nothing in the app
    # reads it any more.
    status = Column(String, default=STATUS_NEEDS_ENRICHMENT, index=True)
    status_note = Column(String)
    # Why the last attempt to draft a message produced nothing. Separate from
    # status_note, which recompute_status owns: a model that timed out says
    # nothing about whether this contact has been researched, and writing both
    # into one field made each overwrite the other.
    draft_note = Column(String)

    # --- deliberately not approaching this contact.
    #
    # Distinct from a finished sequence and from a skipped step: those record
    # work done, this records a decision not to do it. Kept on the person
    # rather than as a step, because it is true of them whatever stage they had
    # reached, and it has to survive a re-import — a rejected contact appearing
    # back in New after someone re-uploads the export is the whole point of
    # storing it. Reversible; see outreach.restore.
    outreach_rejected_at = Column(DateTime, index=True)
    outreach_reject_reason = Column(String)

    imported_at = Column(DateTime, default=utcnow)

    # --- designation is one of the three pillars, and the CSV's copy of it
    #     goes stale. When research turns up a different title, keep both and
    #     flag the mismatch rather than silently trusting either one.
    title_observed = Column(String)
    title_observed_source = Column(String)
    title_observed_at = Column(DateTime)

    # --- the profile they actually post from, when it isn't the one the file
    #     records. Vanity URLs get changed and exports go stale, so a post can
    #     be proven theirs (name in the author slug, employer in the post) while
    #     sitting under a URL the CSV has never seen. Kept beside linkedin_url
    #     rather than replacing it, for the same reason as title_observed: the
    #     file is the source for what it contains, and the reader decides.
    linkedin_observed = Column(String)
    linkedin_observed_source = Column(String)
    linkedin_observed_at = Column(DateTime)

    # --- a standalone re-run of just the LinkedIn Activity panel, separate
    #     from the once-only Research button below. LinkedIn's own feed moves
    #     daily, so a search-found activity list from weeks ago goes stale in
    #     a way the rest of the record doesn't — see can_refresh_linkedin and
    #     pipeline.refresh_linkedin. `linkedin_refreshing` guards against a
    #     second click landing mid-run, the same job research_status/RUNNING
    #     does for the full pipeline, but kept separate so refreshing this one
    #     panel doesn't flip the whole record back to "Researching…".
    # Which CSV this contact arrived on. See models.Import.
    import_id = Column(Integer, ForeignKey("imports.id"), index=True)
    source_import = relationship("Import", back_populates="people")

    linkedin_refreshing = Column(Boolean, default=False)
    linkedin_refreshed_at = Column(DateTime)
    linkedin_refresh_error = Column(String)

    # The POSTS panel, as opposed to linkedin_source above, which is about
    # where the profile URL came from. See db.ADDED_COLUMNS.
    linkedin_posts_source = Column(String)      # "apify" | "firecrawl"
    linkedin_posts_checked_at = Column(DateTime)
    linkedin_posts_note = Column(String)        # e.g. "no posts on this profile"

    @property
    def linkedin_checked(self):
        """Whether anyone has actually looked for this contact's posts.

        An empty panel means two completely different things — nobody has
        looked, or we looked and they do not post — and the reader can only
        act on the second. Without this they render the same.
        """
        return self.linkedin_posts_checked_at is not None

    # --- derived: the "Current Focus" sentence under the interest chips
    focus_line = Column(Text)
    focus_generated_at = Column(DateTime)
    focus_model = Column(String)

    activities = relationship(
        "LinkedInActivity",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="LinkedInActivity.rank",
    )
    findings = relationship(
        "WebFinding",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="WebFinding.rank",
    )
    outreach_steps = relationship(
        "OutreachStep",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="OutreachStep.order_index",
    )
    interests = relationship(
        "Interest",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="Interest.rank",
    )
    # Suggested comments and emails. Cascaded, so deleting a contact does not
    # leave their drafts behind as orphans nothing can reach.
    drafts = relationship(
        "OutreachDraft",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="OutreachDraft.variant",
    )
    # What happened to those drafts. Newest first, because every reader of this
    # wants the last answer, not the first.
    draft_feedback = relationship(
        "DraftFeedback",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="DraftFeedback.created_at.desc()",
    )
    # Stop asking about this one contact. The app-wide off switch is a setting;
    # this is for the lead you are handling differently.
    feedback_opt_out = Column(Boolean, default=False)

    def feedback_for(self, kind, step_key=None, activity_id=None):
        """The answer already given for this draft, or None.

        Keyed the way the draft itself is: an email by its step, a comment by
        the post it replies to. Without the second key, answering comment_1
        would silently answer comment_2 as well.
        """
        for row in self.draft_feedback:
            if row.kind != kind:
                continue
            if activity_id is not None and row.activity_id != activity_id:
                continue
            if activity_id is None and step_key and row.step_key != step_key:
                continue
            return row
        return None

    # ---- outreach sequence. See app/outreach.py for the sequence itself; the
    #      properties here answer only "where is this contact in it", which is
    #      what the list columns and the notification count are built on.

    @property
    def outreach_open(self):
        """The one step still open, or None. Never more than one by design."""
        for step in self.outreach_steps:
            if step.is_open:
                return step
        return None

    @property
    def template_email(self):
        """The approved outreach email for this contact, or why it cannot be.

        Composed, not generated — see app/email_template.py. Derived on read
        like every other status here, so research landing later changes the
        answer without anything needing to be regenerated.
        """
        from app.email_template import compose
        return compose(self)

    @property
    def outreach_rejected(self):
        return self.outreach_rejected_at is not None

    @property
    def outreach_stage(self):
        from app.outreach import (
            STAGE_NEW, STAGE_IN_PROGRESS, STAGE_DONE, STAGE_REJECTED,
        )
        # Rejection wins over everything else. A contact turned down halfway
        # through is rejected, not in progress, or their open step keeps
        # arriving in today's list.
        if self.outreach_rejected:
            return STAGE_REJECTED
        if not self.outreach_steps:
            return STAGE_NEW
        return STAGE_IN_PROGRESS if self.outreach_open else STAGE_DONE

    @property
    def outreach_stage_label(self):
        from app.outreach import STAGE_LABELS
        return STAGE_LABELS.get(self.outreach_stage, self.outreach_stage)

    @property
    def outreach_done_count(self):
        """Steps actually carried out. Skipped ones do not count."""
        return sum(1 for s in self.outreach_steps if s.done_at)

    @property
    def outreach_skipped_count(self):
        return sum(1 for s in self.outreach_steps if s.skipped_at)

    @property
    def outreach_closed_count(self):
        """Steps no longer open — done or skipped.

        What progress should be measured against, because a skipped step is a
        step dealt with: a contact who skipped all four read "0 / 4" beside a
        badge saying "Sequence complete". outreach_done_count is kept for
        anything that means strictly work performed, and the panel shows the
        split in a tooltip so the two are never confused.
        """
        return sum(1 for s in self.outreach_steps
                   if s.done_at or s.skipped_at)

    @property
    def outreach_total(self):
        from app.outreach import SEQUENCE
        return len(SEQUENCE)

    # ---- segmenting. Derived on read from the CSV's own fields, never stored,
    #      so a re-upload or an enrichment run cannot leave a stale segment
    #      behind. See app/segments.py for how the mapping is decided.

    @property
    def category(self):
        """(key, label) - Education, Medical & Health, Engineering, ..."""
        from app.segments import category_of
        return category_of(self)

    @property
    def category_label(self):
        return self.category[1]

    @property
    def segment_countries(self):
        """Every country this contact can be filtered on: theirs, and their
        employer's. They are different facts and often differ."""
        from app.segments import countries_of
        return countries_of(self)

    @property
    def opportunities(self):
        """What Screwdriver can offer this contact's employer.

        Matched against fields already on the record — see app/opportunities.py.
        Computed on read, so it tracks the data rather than going stale, and it
        can be empty: a capability with nothing to point at is left out.
        """
        from app.opportunities import for_person
        return for_person(self)

    @property
    def profile_summary(self):
        """The narrative paragraph shown on the dashboard and the person page.

        Composed from fields already on the record rather than generated, so it
        exists for every contact and costs nothing — see app/summary.py for why
        that choice was made. Computed on read, so it never goes stale against
        the data it describes.
        """
        from app.summary import compose
        return compose(self)

    @property
    def full_name(self):
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def initials(self):
        """No photo in the CSV, and LinkedIn photos can't be hotlinked or
        reused — so the avatar is initials."""
        a = (self.first_name or " ")[:1].upper()
        b = (self.last_name or " ")[:1].upper()
        return (a + b).strip() or "?"

    @property
    def location(self):
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ", ".join(parts) if parts else None

    @property
    def status_label(self):
        return STATUS_LABELS.get(self.enrichment_status, self.enrichment_status)

    @property
    def research_label(self):
        return RESEARCH_LABELS.get(self.research_status, self.research_status)

    @property
    def title_drift(self):
        """True when the web says a different job title than the file does."""
        if not (self.title and self.title_observed):
            return False
        return _title_key(self.title) != _title_key(self.title_observed)

    @property
    def linkedin_drift(self):
        """True when they post from a different profile than the file records.

        Means the URL in the header — and the "Open their LinkedIn" link on the
        outreach board — points at a profile that is not the one publishing
        their posts.
        """
        if not (self.linkedin_url and self.linkedin_observed):
            return False
        return _profile_key(self.linkedin_url) != _profile_key(self.linkedin_observed)

    def missing_critical(self):
        """The identity gaps that hold a contact at Needs Enrichment.

        Only fields that say *who this person is* and how to reach them. Each
        one is a thing enrichment could actually go and fill.

        Designation is here because it is one of the three pillars this tool is
        built on (Company · Designation · Person) and it comes from the file, so
        a blank one is a real hole in the record. Drop it from this list if you'd
        rather treat it as optional — nothing else has to change.
        """
        gaps = []
        if not self.email:
            gaps.append("email")
        if not self.linkedin_url:
            gaps.append("LinkedIn URL")
        if not self.title:
            gaps.append("designation")
        if self.company and not self.company.website:
            gaps.append("company website")
        return gaps

    def missing_optional(self):
        """Gaps worth showing that must never set Needs Enrichment.

        None of these describe the person's identity:

        - Direct phone: the Apollo export carries one for nobody, so counting it
          would hold every contact at Needs Enrichment forever.
        - Company description: a fact about the employer, and one research
          fills in — not something missing from this person's record.
        - Research and LinkedIn activity: the absence of these means nobody has
          looked yet, or there was nothing public to find. That's research
          status, which is now tracked separately.
        """
        gaps = []
        if not self.phone_direct:
            gaps.append("direct phone (optional)")
        if self.company and not self.company.description:
            gaps.append("company description (optional)")
        if not self.activities:
            gaps.append("LinkedIn activity (optional)")
        return gaps

    def missing_fields(self, blocking_only=False):
        """Everything still outstanding; critical first."""
        if blocking_only:
            return self.missing_critical()
        return self.missing_critical() + self.missing_optional()

    @property
    def is_complete(self):
        """Identity complete. Says nothing about whether research has run."""
        return not self.missing_critical()

    @property
    def is_researched(self):
        """Whether a research run has happened for this contact.

        A finished run is the fact, whatever it turned up. The fallback to
        stored rows is for contacts researched before research_completed_at
        existed: their run left no timestamp, only its results.
        """
        if self.research_completed_at:
            return True
        return bool(self.findings or self.activities)

    @property
    def outreach_prep(self):
        """What starting a sequence would actually have to work with.

        The New sheet asks this before anyone presses Start. `is_researched`
        is an OR over findings and activities, so a contact can show
        "Researched" on company findings alone — while the sequence opens on
        two comment steps that need a post of their own. The badge on its own
        therefore cannot be planned from, and someone only discovers the gap
        after the contact has already moved to In progress.

        Only posts and reposts count. A comment they left on someone else's
        post, or a mention of them, is a finding about them rather than
        something of theirs to comment on.

        Returns (state, note): state picks the colour, note is the phrase for
        the row, or None where the badge already says everything.
        """
        if self.research_status == RESEARCH_RUNNING:
            return "running", None
        if self.research_status == RESEARCH_FAILED:
            return "failed", "research failed — retry on their page"
        if not self.is_researched:
            return "none", "nothing found to comment on yet"
        posts = sum(1 for a in self.activities
                    if a.activity_type == ACTIVITY_POST)
        reposts = sum(1 for a in self.activities
                      if a.activity_type == ACTIVITY_REPOST)
        if not (posts or reposts):
            return "thin", "nothing of theirs to comment on — both comment steps open empty"
        # Counted apart, because ACTIVITY_REPOST's own note in this file is
        # that a repost is not a post of theirs and merging the two overstates
        # what they actually wrote. Both can be commented on, so both appear.
        parts = []
        if posts:
            parts.append("%d post%s" % (posts, "" if posts == 1 else "s"))
        if reposts:
            parts.append("%d repost%s" % (reposts, "" if reposts == 1 else "s"))
        return "ready", "%s to comment on" % ", ".join(parts)

    @property
    def can_research(self):
        """Whether to offer the Research button.

        Offered when nothing has been found yet or the last attempt failed.
        Not offered while a run is in progress, and not for a contact that
        already has results — re-running costs API credits, so that is a
        deliberate act via the CLI's --force rather than a button anyone can
        hit twice.
        """
        return self.research_status in (RESEARCH_PENDING, RESEARCH_FAILED)

    @property
    def can_refresh_linkedin(self):
        """Whether to offer a standalone LinkedIn refresh.

        Unlike can_research, this stays offered after the first run and every
        run after — LinkedIn Activity is the one panel here that's genuinely
        time-sensitive, so re-checking it isn't the one-shot, credit-conscious
        act the full Research button is. Only withheld while the initial
        Research hasn't completed at least once (nothing to refresh yet — use
        Research instead) or while a run of either kind is already in flight.
        """
        return (
            self.research_status == RESEARCH_DONE
            and not self.linkedin_refreshing
        )

    def recompute_status(self, research_failed=False, note=None):
        """The single place either status is decided.

        Previously five call sites each ran their own `status = COMPLETE if
        is_complete else NEEDS_ENRICHMENT`, so a change to the rule had to be
        made five times and a research failure overwrote the enrichment answer.
        """
        self.enrichment_status = (
            STATUS_COMPLETE if self.is_complete else STATUS_NEEDS_ENRICHMENT
        )
        if research_failed:
            self.research_status = RESEARCH_FAILED
        elif self.is_researched:
            self.research_status = RESEARCH_DONE
        elif self.research_status != RESEARCH_RUNNING:
            # A run in progress stays visible. Without this guard an import or
            # an activity edit mid-run would reset the badge to "Not
            # researched" and the button would offer to start a second one.
            self.research_status = RESEARCH_PENDING
        if note is not None:
            self.status_note = note
        self.status = self.enrichment_status   # legacy mirror
        return self.enrichment_status


class Import(Base):
    """One CSV upload, and what it did.

    Kept so the app can say where a contact came from and so an upload can be
    undone. The counts are the importer's own — recorded rather than
    recomputed, because "how many did this file create" stops being
    answerable the moment a later file updates some of the same people.
    """
    __tablename__ = "imports"

    id = Column(Integer, primary_key=True)
    filename = Column(String)
    uploaded_at = Column(DateTime, default=utcnow, index=True)
    size_bytes = Column(Integer, default=0)

    # Straight from importer.import_csv's summary.
    rows = Column(Integer, default=0)          # data rows in the file
    created = Column(Integer, default=0)       # people it added
    updated = Column(Integer, default=0)       # people it matched and filled in
    failed = Column(Integer, default=0)        # rows it could not use
    needs_enrichment = Column(Integer, default=0)
    columns_matched = Column(Integer, default=0)
    columns_seen = Column(Integer, default=0)
    notes = Column(Text)                       # newline-joined
    errors = Column(Text)                      # newline-joined

    people = relationship("Person", back_populates="source_import")

    @property
    def label(self):
        return self.filename or "upload.csv"

    @property
    def still_here(self):
        """How many of the contacts it created are still in the database.

        Not the same as `created`: contacts get deleted, and a later upload
        can merge two records. The delete button has to offer the real number
        rather than the one from the day of the upload.
        """
        return len(self.people)

    @property
    def size_label(self):
        n = self.size_bytes or 0
        if n >= 1024 * 1024:
            return f"{n / (1024 * 1024):.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.0f} KB"
        return f"{n} bytes"

    @property
    def note_lines(self):
        return [n for n in (self.notes or "").split("\n") if n.strip()]

    @property
    def error_lines(self):
        return [e for e in (self.errors or "").split("\n") if e.strip()]


class LinkedInActivity(Base):
    """
    Either pasted by hand on the person's page, or found in the web search
    index by research.find_linkedin_activity. The tool never logs into
    LinkedIn or fetches a linkedin.com page for either route.

    added_by says which one produced the row: "pasted" or "search". A searched
    row carries the query and fetch time, the same provenance a WebFinding
    carries, because the app claims it for anything it fetched itself.
    """
    __tablename__ = "linkedin_activities"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="activities")

    activity_type = Column(String, default=ACTIVITY_POST)
    url = Column(String)
    text = Column(Text)
    activity_date = Column(Date)      # the date shown on the post
    rank = Column(Integer, default=0)  # 1..5, the display order

    added_at = Column(DateTime, default=utcnow)
    added_by = Column(String, default="manual")

    fetched_at = Column(DateTime)      # set only for rows found by search
    source_query = Column(String)      # the query that surfaced it — auditable

    # ---- the recommended comment for this post. Steps 1 and 2 of the outreach
    #      sequence are "comment on their post", so the draft lives beside the
    #      post it replies to. suggested_note carries the reason when no draft
    #      was possible — usually that the indexed snippet is the contact's
    #      headline rather than the post. See app/messages.py.
    suggested_comment = Column(Text)
    suggested_note = Column(String)
    suggested_at = Column(DateTime)
    suggested_model = Column(String)

    # "snippet" | "headline" | None. See db.ADDED_COLUMNS.
    text_source = Column(String)

    @property
    def text_is_headline(self):
        """The text is the post's opening line, not its body.

        Firecrawl will not fetch a LinkedIn post, so for most rows the only
        words we have are the ones LinkedIn put in the URL and the title. They
        are the author's, and they are the topic — but a comment written from
        them must not pretend to have read the rest.
        """
        return self.text_source == "headline"

    # Same two tiers the web findings use. True only when the post URL's author
    # segment matches this contact's profile slug; False for anything found by
    # name, which may be a namesake. Hand-pasted rows are trusted outright — a
    # person judged them.
    corroborated = Column(Boolean, default=False)
    corroboration = Column(String)

    # Comment drafts written about this post. Cascaded, because a draft is a
    # reply to a specific post: once the post is gone — deleted by hand, or
    # replaced when research re-runs — the draft references something we no
    # longer hold and must not still be offered as sendable.
    drafts = relationship(
        "OutreachDraft",
        back_populates="activity",
        cascade="all, delete-orphan",
    )

    @property
    def from_search(self):
        """Fetched automatically, rather than pasted in by a person.

        Both providers count. The name predates Apify and is kept because it
        is what the rest of the code asks — the question it answers is "may a
        refresh replace this row", and the answer is yes for anything a
        provider produced and no for anything typed by hand.
        """
        return self.added_by in ("search", "apify")

    @property
    def from_apify(self):
        """Read off the contact's own profile, not matched by name."""
        return self.added_by == "apify"

    @property
    def trusted(self):
        """Whether this row may be used to infer anything about the person."""
        return not self.from_search or bool(self.corroborated)

    @property
    def type_label(self):
        return ACTIVITY_LABELS.get(self.activity_type, self.activity_type)


class WebFinding(Base):
    """Automated: Google / web research. Provenance is mandatory here."""
    __tablename__ = "web_findings"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="findings")

    title = Column(String)
    url = Column(String)
    snippet = Column(Text)
    kind = Column(String)             # article / interview / event / news / profile
    published_date = Column(Date)     # often unknown from search results
    rank = Column(Integer, default=0)

    fetched_at = Column(DateTime, default=utcnow)
    source_query = Column(String)     # the query that surfaced it — auditable

    # Whether this result is demonstrably about *this* person. A name in a
    # search result cannot be told apart from a namesake's, so anything not
    # corroborated is shown last and never used to derive an interest chip.
    corroborated = Column(Boolean, default=False)
    corroboration = Column(String)    # what tied it to them, for the tooltip

    # True for a row that isn't about this person at all — company-news
    # filler used when the searches above didn't turn up enough that could be
    # tied to the contact. Always corroborated=False, by construction: it
    # never derives an interest chip or an inferred employer (both skip
    # anything uncorroborated already), it just keeps the panel from sitting
    # empty. See research.research_person.
    about_company = Column(Boolean, default=False)

    @property
    def clean_snippet(self):
        """`snippet` with scraped markup removed.

        The index returns page content for a page it can fetch, so a snippet
        arrives carrying headings, image tags and link syntax. Rendered raw it
        reads as "# The Assembly Canada ## Speakers and Task Force [Anwar
        Chaudhry](https://...)".
        """
        from app.summary import clean_snippet
        return clean_snippet(self.snippet)


class OutreachStep(Base):
    """One step of the manual outreach sequence for one contact.

    Rows are created as the sequence progresses, not all at once: a step's due
    date is measured from the day the previous step was actually completed, so
    it cannot be known in advance. See app/outreach.py.

    A step is "open" until it is either done or skipped. Exactly one step per
    contact is open at a time, which is what makes "today's tasks" a simple
    query rather than a scheduling problem.
    """
    __tablename__ = "outreach_steps"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="outreach_steps")

    step_key = Column(String, index=True)   # comment_1 / comment_2 / connect / email
    order_index = Column(Integer, default=0)

    due_date = Column(Date, index=True)
    done_at = Column(DateTime)
    skipped_at = Column(DateTime)
    skip_reason = Column(String)

    # What was actually done — which post was commented on, what the email said.
    # The sequence is only useful on the second pass if it records that.
    note = Column(Text)

    created_at = Column(DateTime, default=utcnow)

    @property
    def is_open(self):
        return self.done_at is None and self.skipped_at is None

    @property
    def label(self):
        from app.outreach import label_for
        return label_for(self.step_key)

    @property
    def hint(self):
        from app.outreach import step_def
        d = step_def(self.step_key)
        return d.get("hint") if d else None

    @property
    def days_until_due(self):
        """Negative when overdue, 0 today, positive when still to come."""
        if not self.due_date:
            return None
        return (self.due_date - utcnow().date()).days

    @property
    def is_due(self):
        """Due today or earlier — what "pending today" means."""
        d = self.days_until_due
        return self.is_open and d is not None and d <= 0

    @property
    def is_overdue(self):
        d = self.days_until_due
        return self.is_open and d is not None and d < 0

    @property
    def blocked_reason(self):
        """Why this step can't be actioned yet, or None.

        A property rather than a template filter so the board and the person
        page get the same answer without either registering anything.
        """
        from app.outreach import blocked_reason
        return blocked_reason(self.person, self.step_key) if self.person else None

    # ---- the message this step needs written
    #
    # Derived on read, like every other status in this app, so a draft can
    # never be stale relative to the evidence it was written from: research
    # that lands after a draft changes the verdict on the next render.

    @property
    def wants_draft(self):
        """Whether this step is one a message can be drafted for.

        `connect` is excluded: a connection request has no body to write. The
        step still carries its hint, which is all it needs.
        """
        return self.step_key in ("comment_1", "comment_2", "email")

    @property
    def draft_kind(self):
        return "email" if self.step_key == "email" else "comment"

    @property
    def prefer_index(self):
        """Which post a comment step should reach for.

        The second comment must not land on the post the first one did, so it
        takes the next one down. See personalisation.comment_target.
        """
        return 1 if self.step_key == "comment_2" else 0

    @property
    def personalisation(self):
        """Whether a message for this step can honestly be personalised.

        Costs nothing — no model call, no network. That is the point: the panel
        can say a contact is too thin to write to before anyone spends a
        request finding out.
        """
        from app.personalisation import assess
        if not (self.person and self.wants_draft):
            return None
        return assess(self.person, self.draft_kind,
                      prefer_index=self.prefer_index)

    @property
    def written_email(self):
        """The email drafted for this step under the skills/ brief, if any.

        Deliberately not draft_options: that one is gated on wants_draft, which
        runs the personalisation evidence check belonging to the older
        multi-option path. The brief does its own refusing — it returns a
        stated reason rather than a forced email — so gating it a second time
        here would hide a draft that the brief already decided was fair.
        """
        from app.messages import options_for
        if not self.person:
            return None
        for draft in options_for(self.person, self.step_key):
            if draft.kind == "email":
                return draft
        return None

    @property
    def draft_options(self):
        """Stored options for this step's kind of message."""
        from app.messages import options_for
        if not (self.person and self.wants_draft):
            return []
        return options_for(self.person, self.step_key)

    @property
    def state(self):
        if self.done_at:
            return "done"
        if self.skipped_at:
            return "skipped"
        if self.is_overdue:
            return "overdue"
        if self.is_due:
            return "due"
        return "waiting"


class OutreachDraft(Base):
    """One suggested option for one outreach step: a comment, or an email.

    Several rows per (person, kind) by design — the point is a choice. A person
    picks the angle that fits what they actually know, which is a judgement the
    model should not be making alone, and having three on screen makes the
    weakest one visibly weak.

    Provenance is mandatory here for the same reason it is on a web finding:
    `basis` names the signals the draft was written from, so a reader can tell
    a message grounded in the contact's own post from one leaning on their
    employer's marketing copy. A draft with an empty basis is not stored — see
    messages._keep.

    Regenerating replaces every row for that (person, kind): the options are a
    set, and mixing a warm variant from an hour ago with two formal ones now
    would make `tone` a lie about what is on screen.
    """
    __tablename__ = "outreach_drafts"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="drafts")

    kind = Column(String, index=True)       # "comment" | "email"
    step_key = Column(String)               # the step it was drafted for
    variant = Column(Integer, default=1)    # 1..n, the order shown

    # A short name for what this option leads with, so the three are
    # distinguishable at a glance rather than three grey boxes.
    angle = Column(String)

    subject = Column(String)                # email only
    body = Column(Text)

    # The knobs the reader turned. Stored so the panel can show what produced
    # what is on screen, and so a regenerate can default to the last choice.
    tone = Column(String)                   # email only
    length = Column(String)

    # Which signals this was written from, and the post it replies to.
    basis = Column(Text)
    activity_id = Column(Integer, ForeignKey("linkedin_activities.id"))
    activity = relationship("LinkedInActivity", back_populates="drafts")

    model = Column(String)
    generated_at = Column(DateTime, default=utcnow)

    @property
    def char_count(self):
        return len(self.body or "")

    @property
    def word_count(self):
        return len((self.body or "").split())

    @property
    def over_linkedin_limit(self):
        """Whether this would be refused by LinkedIn's comment box.

        Enforced before storing too (messages.reject_reason), so this should
        never be True. It is here so a row written by an older version, or one
        edited by hand in the DB, still shows the problem rather than failing
        silently in the paste.
        """
        from app.messages import LINKEDIN_COMMENT_MAX_CHARS
        return self.kind == "comment" and self.char_count > LINKEDIN_COMMENT_MAX_CHARS

    @property
    def basis_lines(self):
        """`basis` as a list, for rendering one signal per line."""
        return [b.strip() for b in (self.basis or "").split(";") if b.strip()]


class CompanyFinding(Base):
    """What the web says about a company, as opposed to about a person.

    Its own table rather than a flag on WebFinding, because the fact belongs to
    the employer and not to whoever happens to work there. Three contacts at
    Axonify share one Axonify; storing the same announcement three times would
    pay for the same search three times, show three copies on the board, and
    let the three drift apart when one is re-researched and the others are not.

    Provenance is mandatory, exactly as on a web finding: the URL, the query
    that surfaced it, and when it was fetched. `confirmation` records what tied
    the result to this company rather than to a namesake — see
    research._about_company. A row that clears nothing is not stored.
    """
    __tablename__ = "company_findings"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), index=True)
    company = relationship("Company", back_populates="findings")

    title = Column(String)
    url = Column(String)
    snippet = Column(Text)
    kind = Column(String)              # article / interview / event / news / profile
    published_date = Column(Date)      # usually unknown from a search result
    rank = Column(Integer, default=0)

    source_query = Column(String)      # auditable: what we asked
    fetched_at = Column(DateTime, default=utcnow)

    # Why this result is about THIS company and not one with a similar name.
    confirmation = Column(String)

    @property
    def source_domain(self):
        """The host this came from — the key the panel groups on."""
        if not self.url:
            return ""
        return self.url.split("//")[-1].split("/")[0].replace("www.", "").lower()

    @property
    def on_own_site(self):
        return self.confirmation == "on the company's own site"

    @property
    def clean_snippet(self):
        from app.summary import clean_snippet
        return clean_snippet(self.snippet)


class Interest(Base):
    """The chips in the Interests / Current Focus panel."""
    __tablename__ = "interests"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="interests")

    label = Column(String)
    # Which evidence produced this chip — so a chip is never an unsourced guess.
    evidence = Column(Text)
    derived_from = Column(String)     # "linkedin" | "web" | "both"
    rank = Column(Integer, default=0)
    generated_at = Column(DateTime, default=utcnow)


# --------------------------------------------------------------- the profile
#
# A model call carries no memory of the last one. So what the app knows about
# the person using it lives here, and app/profile.py renders the relevant part
# into the system prompt on every draft. Nothing is remembered on the far side.

PROFILE_CATEGORIES = ("fact", "preference", "behaviour", "history")
PROFILE_SOURCES = ("told", "inferred", "summarised")
PROFILE_SCOPES = ("global", "vertical", "person")

CATEGORY_LABELS = {
    "fact": "Who you are",
    "preference": "What you have told it",
    "behaviour": "What your edits show",
    "history": "What has happened so far",
}


class ProfileEntry(Base):
    """One remembered thing about the sender.

    Four categories, because they are not equally trustworthy and must not be
    updated by the same rule. Something you typed outranks something the app
    worked out, permanently and by construction: an inference can be wrong, and
    the person it would be wrong about is sitting right there.
    """
    __tablename__ = "profile_entries"

    id = Column(Integer, primary_key=True)

    category = Column(String, index=True)   # PROFILE_CATEGORIES
    # Only facts are keyed — "role", "company", "targets" — so re-stating one
    # replaces it rather than stacking a second answer to the same question.
    key = Column(String)
    text = Column(Text)                     # the sentence that gets injected

    # told      — you typed it. True on arrival, never auto-evicted.
    # inferred  — worked out from your edits. Must be confirmed before it counts.
    # summarised— the rolling history roll-up. Replaces itself.
    source = Column(String, index=True)
    # What produced it. Required for anything not `told`, and enforced in
    # app/profile.py rather than merely asked for — the same rule interests.py
    # applies to a chip.
    evidence = Column(Text)

    scope = Column(String, default="global", index=True)   # PROFILE_SCOPES
    scope_key = Column(String)              # None | a vertical | a person id
    applies_to = Column(String, default="both")            # email|comment|both

    # A mechanical entry names a literal phrase, so the existing validators can
    # enforce it instead of the prompt merely requesting it. Anything that needs
    # judgement is not mechanical and can only be injected.
    mechanical = Column(Boolean, default=False)
    pattern = Column(String)

    times_seen = Column(Integer, default=1)
    active = Column(Boolean, default=False, index=True)

    model = Column(String)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)

    @property
    def category_label(self):
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def locked(self):
        """Stated by a person, so the app does not get to retire it."""
        return self.source == "told"

    @property
    def scope_label(self):
        if self.scope == "global":
            return "every draft"
        if self.scope == "vertical":
            return f"{self.scope_key} leads"
        return "this contact"


class DraftFeedback(Base):
    """What happened to one draft: used, changed, or not used.

    The raw log. Kept in full because it is a local SQLite file and costs
    nothing, and **never injected into a prompt** — only the compressed summary
    in ProfileEntry is. That split is what keeps the prompt from growing with
    every message ever sent.

    The draft is stored as a snapshot rather than a foreign key, because the two
    drafting paths write to two different tables (LinkedInActivity for comments,
    OutreachDraft for the email) and either can be regenerated out from under a
    feedback row. A lesson has to keep the text it was drawn from.
    """
    __tablename__ = "draft_feedback"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="draft_feedback")

    kind = Column(String, index=True)       # "email" | "comment"
    step_key = Column(String)
    activity_id = Column(Integer, ForeignKey("linkedin_activities.id"))

    # used     — sent as written. A positive signal, and the only one there is.
    # edited   — sent, but changed. The most informative outcome.
    # unused   — not sent at all.
    # skipped  — declined to say. Recorded so the question stops being asked.
    outcome = Column(String, index=True)

    draft_subject = Column(String)
    draft_body = Column(Text)
    final_subject = Column(String)
    final_body = Column(Text)
    reason = Column(Text)

    # Denormalised on purpose: re-categorising a company later must not silently
    # retag what was true when the draft was written.
    vertical = Column(String)

    provider = Column(String)
    model = Column(String)
    # Which ProfileEntry ids were in the prompt that produced this draft, so
    # "which memory influenced this" is answerable after the fact.
    profile_applied = Column(String)

    created_at = Column(DateTime, default=utcnow, index=True)

    @property
    def changed(self):
        return self.outcome == "edited"

    @property
    def word_delta(self):
        """How much shorter or longer what you sent was. None unless edited."""
        if not (self.draft_body and self.final_body):
            return None
        return len(self.final_body.split()) - len(self.draft_body.split())
