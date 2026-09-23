import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base

DATA_DIR = os.environ.get(
    "PEOPLEINTEL_DATA",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "peopleintel.db")

# SQLite is fine here: this app is deployed on a host with a persistent disk
# (Railway / Render / Fly), not on a serverless platform with an ephemeral
# filesystem. Swapping to Postgres later is a URL change, nothing more.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Columns added after the first release. create_all() only creates missing
# tables, never missing columns, so a database made by an earlier version needs
# them added or every query against the table fails. There is no migration tool
# here on purpose; this stays a short, idempotent list.
ADDED_COLUMNS = {
    "linkedin_activities": [
        ("fetched_at", "DATETIME"),
        ("source_query", "VARCHAR"),
        ("corroborated", "BOOLEAN DEFAULT 0"),
        ("corroboration", "VARCHAR"),
        ("suggested_comment", "TEXT"),
        ("suggested_note", "VARCHAR"),
        ("suggested_at", "DATETIME"),
        ("suggested_model", "VARCHAR"),
        # Where `text` came from: "snippet" (the search result's description),
        # "headline" (the post's opening, recovered from its URL or title), or
        # null for a pasted row, which is the real thing. The comment prompt is
        # told which, because a headline is a topic and a snippet is a body.
        ("text_source", "VARCHAR"),
    ],
    "people": [
        # Which upload this contact arrived on. Null for anyone imported
        # before uploads were recorded, which the Reports page reports as its
        # own group rather than pretending they came from nowhere.
        ("import_id", "INTEGER"),
        # Which provider last filled the LinkedIn POSTS panel, when we last
        # looked, and what there was to say about it. All three are needed to
        # tell "no posts on this profile" apart from "never checked": the
        # panel renders identically otherwise, and the first is an answer
        # while the second is a to-do.
        #
        # Named apart from linkedin_source above, which is a different thing
        # entirely — that one records where the profile URL itself came from.
        ("linkedin_posts_source", "VARCHAR"),
        ("linkedin_posts_checked_at", "DATETIME"),
        ("linkedin_posts_note", "VARCHAR"),
        ("enrichment_status", "VARCHAR"),
        ("research_status", "VARCHAR"),
        ("email_source", "VARCHAR"),
        ("email_source_url", "VARCHAR"),
        ("email_found_at", "DATETIME"),
        ("linkedin_source", "VARCHAR"),
        ("linkedin_found_at", "DATETIME"),
        ("identity_note", "VARCHAR"),
        ("draft_note", "VARCHAR"),
        ("outreach_rejected_at", "DATETIME"),
        ("outreach_reject_reason", "VARCHAR"),
        ("linkedin_observed", "VARCHAR"),
        ("linkedin_observed_source", "VARCHAR"),
        ("linkedin_observed_at", "DATETIME"),
        ("linkedin_refreshing", "BOOLEAN DEFAULT 0"),
        ("linkedin_refreshed_at", "DATETIME"),
        ("linkedin_refresh_error", "VARCHAR"),
        ("research_completed_at", "DATETIME"),
        ("feedback_opt_out", "BOOLEAN DEFAULT 0"),
    ],
    "companies": [
        ("web_checked_at", "DATETIME"),
        ("web_note", "VARCHAR"),
    ],
    "web_findings": [
        ("corroborated", "BOOLEAN DEFAULT 0"),
        ("corroboration", "VARCHAR"),
        ("about_company", "BOOLEAN DEFAULT 0"),
    ],
}


def _add_missing_columns():
    with engine.begin() as conn:
        for table, columns in ADDED_COLUMNS.items():
            present = {
                r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if not present:
                continue  # table isn't there yet; create_all will make it whole
            for name, sqltype in columns:
                if name not in present:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}"
                    )


def init_db():
    Base.metadata.create_all(engine)
    _add_missing_columns()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
