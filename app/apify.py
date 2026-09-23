"""
LinkedIn posts, pulled from the contact's own profile through Apify.

Why this exists alongside app/research.py: Firecrawl cannot fetch LinkedIn at
all. A scrape of any linkedin.com URL comes back 403 "we do not support this
site", and `scrapeOptions` on a search returns the result with no content. So
everything the Firecrawl path knew about a post came from a search index —
the URL, the page title and a one-line description that is usually LinkedIn's
attribution block rather than the post. Worse, a search for a name returns
other people's posts that merely mention it, which is why most candidate rows
had to be discarded as possible namesakes.

Apify reads the profile itself. The input is the LinkedIn URL already on the
record — the one that came in on the CSV — so authorship is not in question
and none of the namesake machinery applies: every post returned is that
profile's. The post body and its exact date come back as data.

The division of labour is deliberate and should stay that way:

    Firecrawl  — Google/web search: company descriptions, news, the web panel.
    Apify      — LinkedIn posts, and only through the profile URL.

Nothing here talks to an LLM and nothing here writes to the database. It
returns rows in the shape research.find_linkedin_activity returns, so
pipeline.refresh_linkedin can store either without caring which produced them.
"""
import os
from datetime import datetime, timezone

import httpx

# harvestapi/linkedin-profile-posts. The tilde is Apify's URL form of the
# username/name pair.
ACTOR = "harvestapi~linkedin-profile-posts"
RUN_URL = (f"https://api.apify.com/v2/acts/{ACTOR}"
           "/run-sync-get-dataset-items")

KEY_VAR = "APIFY_API_KEY"

# The actor starts a browser and pages a profile, so it is tens of seconds,
# not the two a search takes. `wait` is what Apify will hold the request open
# for; TIMEOUT is our own ceiling and must be the larger of the two or httpx
# gives up while Apify is still willing to answer.
RUN_WAIT_SECONDS = 180
TIMEOUT = 210.0

# Kept in step with pipeline.MAX_ACTIVITIES — the panel shows five.
MAX_POSTS = 5


class ApifyNotConfigured(RuntimeError):
    """No API token. The caller decides whether to fall back or to stop."""


class ApifyRejected(RuntimeError):
    """Apify refused the run: bad token, no credit, or no such actor.

    Distinct from "this profile has no posts", which is not an error and is
    reported as an empty list with a note.
    """


def api_key():
    return (os.environ.get(KEY_VAR) or "").strip()


def configured():
    return bool(api_key())


def profile_url(person):
    """The LinkedIn URL to scrape for this contact, or None.

    Prefers the profile the contact was *observed* posting from over the one
    on the record: a CSV export's vanity URL goes stale when someone changes
    it, and research.find_linkedin_activity records the real one when it finds
    a mismatch. Falls back to the record, which is what the CSV supplied.
    """
    for candidate in (getattr(person, "linkedin_observed", None),
                      getattr(person, "linkedin_url", None)):
        url = (candidate or "").strip()
        if url and "linkedin.com/in/" in url.lower():
            # http:// URLs are common in exports and Apify wants a live one.
            if url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            return url
    return None


def _post_moment(item):
    """When the post went up, as a timezone-aware datetime, or None.

    postedAt.date is an ISO timestamp. postedAt.timestamp is milliseconds when
    it is there. Both are given rather than either being relied on, because a
    missing date is the difference between "their latest post" and a row that
    cannot be ordered at all.
    """
    posted = item.get("postedAt")
    if isinstance(posted, dict):
        raw = posted.get("date") or posted.get("isoDate")
        if raw:
            try:
                text = str(raw).replace("Z", "+00:00")
                moment = datetime.fromisoformat(text)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                return moment
            except (TypeError, ValueError):
                pass
        stamp = posted.get("timestamp")
        if stamp:
            try:
                return datetime.fromtimestamp(int(stamp) / 1000.0,
                                              timezone.utc)
            except (TypeError, ValueError, OSError, OverflowError):
                pass
    return None


def _kind(item):
    """post or repost. The actor returns reposts alongside original posts."""
    if item.get("repost") or item.get("resharedPost") or item.get("reshared"):
        return "repost"
    if str(item.get("type") or "").lower() in ("repost", "reshare", "share"):
        return "repost"
    return "post"


def _text(item):
    """The post's own words, or None when the actor returned none.

    A repost with no added commentary genuinely has no text of the contact's.
    Returning None rather than the original author's words is the point: a
    comment drafted from somebody else's post, attributed to this contact, is
    exactly the mistake the Firecrawl path kept making.
    """
    for field in ("content", "text", "postText", "description"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:4000]
    return None


def _engagement(item):
    """"12 likes · 3 comments", or "" — shown under the row as context."""
    data = item.get("engagement")
    if not isinstance(data, dict):
        return ""
    bits = []
    for field, label in (("likes", "like"), ("comments", "comment"),
                         ("shares", "share")):
        try:
            count = int(data.get(field) or 0)
        except (TypeError, ValueError):
            continue
        if count:
            bits.append(f"{count} {label}{'s' if count != 1 else ''}")
    return " · ".join(bits)


def fetch_posts(url, limit=MAX_POSTS, key=None):
    """The latest posts on one LinkedIn profile.

    Returns (rows, note). `rows` are in research.find_linkedin_activity's
    shape, newest first. `note` is a sentence for the reader when there is
    something to say — an empty profile, or posts that carried no text — and
    "" when the rows speak for themselves.

    Raises ApifyNotConfigured with no token, ApifyRejected when Apify refuses
    the run. A profile with no posts is neither: it is an empty list and a
    note saying so, because "this person does not post" is a real answer and
    the panel should say it rather than look broken.
    """
    token = (key or api_key())
    if not token:
        raise ApifyNotConfigured(
            f"{KEY_VAR} is not set — add it on the Settings page. Without it "
            "LinkedIn posts cannot be fetched; company and web research use "
            "Firecrawl and are unaffected."
        )
    if not url:
        return [], "No LinkedIn profile URL on this contact, so there is "\
                   "nothing to fetch posts from."

    payload = {
        "targetUrls": [url],
        "maxPosts": max(1, int(limit or MAX_POSTS)),
        # Both off: each is extra work for the actor and neither is drafted
        # from. Engagement counts come back on the post itself regardless.
        "scrapeReactions": False,
        "scrapeComments": False,
        "includeReposts": True,
        "includeQuotePosts": True,
    }

    try:
        response = httpx.post(
            RUN_URL,
            params={"token": token, "timeout": RUN_WAIT_SECONDS,
                    "format": "json"},
            json=payload,
            timeout=TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise ApifyRejected(
            f"Apify did not finish within {int(TIMEOUT)}s. The actor starts a "
            "browser, so a slow profile can exceed it; try again."
        ) from exc
    except httpx.HTTPError as exc:
        raise ApifyRejected(f"Could not reach Apify: {exc}") from exc

    if response.status_code in (401, 403):
        raise ApifyRejected(
            f"Apify rejected the token (HTTP {response.status_code}). Check "
            f"{KEY_VAR} on the Settings page."
        )
    if response.status_code == 402:
        raise ApifyRejected(
            "Apify reports no credit left on the account (HTTP 402). Waiting "
            "will not help; top up or wait for the plan to reset."
        )
    if response.status_code == 404:
        raise ApifyRejected(
            f"Apify has no actor {ACTOR!r} (HTTP 404). The account may not "
            "have access to it — open it once on apify.com and try again."
        )
    if response.status_code >= 400:
        raise ApifyRejected(
            f"Apify returned HTTP {response.status_code}: "
            f"{(response.text or '')[:200]}"
        )

    try:
        items = response.json()
    except ValueError as exc:
        raise ApifyRejected("Apify returned something that is not JSON") from exc
    if isinstance(items, dict):            # an error object, or a wrapper
        items = items.get("items") or items.get("data") or []
    if not isinstance(items, list):
        items = []

    fetched = datetime.now(timezone.utc)
    rows, textless = [], 0
    for item in items:
        if not isinstance(item, dict):
            continue
        # The dataset can carry comment and reaction rows when those options
        # are on. They are off above, but a row of another type is still not
        # a post and must not be ranked as one.
        if str(item.get("type") or "post").lower() in ("comment", "reaction"):
            continue
        link = (item.get("linkedinUrl") or item.get("url")
                or item.get("postUrl") or "").strip()
        if not link:
            continue
        moment = _post_moment(item)
        body = _text(item)
        if not body:
            textless += 1
        engagement = _engagement(item)
        rows.append({
            "activity_type": _kind(item),
            "url": link,
            "text": body,
            "activity_date": moment.date() if moment else None,
            "added_by": "apify",
            # Not a judgement call here. The actor was pointed at this
            # contact's own profile, so every row is theirs by construction —
            # which is the whole reason for using it over a name search.
            "corroborated": True,
            "corroboration": ("from their own LinkedIn profile via Apify"
                              + (f"; {engagement}" if engagement else "")),
            "source_query": f"apify:{ACTOR} {url}",
            "fetched_at": fetched,
            "text_source": "post" if body else None,
            "_moment": moment,
        })

    # Newest first. The actor returns profile order, which is usually newest
    # first but is not promised to be, and "the latest post" is the whole
    # reason for this module.
    rows.sort(key=lambda r: (r["_moment"] is None,
                             -r["_moment"].timestamp() if r["_moment"] else 0))
    for i, row in enumerate(rows[:limit], start=1):
        row["rank"] = i
        row.pop("_moment", None)
    rows = rows[:limit]

    if not rows:
        return [], ("Apify found no posts on this LinkedIn profile. That is "
                    "an answer, not a failure — the profile may be private, "
                    "or this contact may not post.")
    if textless == len(rows):
        return rows, ("Apify returned posts but none carried any text — "
                      "reposts with no added comment, or image-only posts. "
                      "There is nothing written to reply to.")
    return rows, ""
