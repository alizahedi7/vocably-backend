"""Handle rules: what a username may be, and how to derive one.

Handles are how sharing, friends and the roster address a person, so they are
the one user-chosen string in this product that other people type. That makes
them a namespace, with the two consequences a namespace has: reserved words, and
no silent reuse.
"""

from __future__ import annotations

import re
from uuid import UUID

#: Lowercase, starts with a letter, 3–20 characters. Mirrors the client's
#: ``AppState.slugifyUsername`` exactly — a handle the app will not let someone
#: type must not be reachable by calling the API directly.
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,19}$")

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 20

#: How much of a handle has to be typed before anyone is looked up.
#:
#: Two, not one: a single letter matches a sizeable fraction of the table and
#: answers a question nobody asked — the searcher has typed one character, they
#: are not yet looking for anybody in particular. It is below
#: :data:`USERNAME_MIN_LENGTH` on purpose, because this is a *prefix* of a
#: handle rather than a handle.
USERNAME_SEARCH_MIN_LENGTH = 2

#: Characters a handle is made of, so a prefix can be checked without knowing
#: whether it is a whole one yet.
_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

#: Names that must never belong to a person. `/join/<code>` and `/users/me` are
#: real paths, and a handle colliding with one is a support ticket waiting to
#: happen; the rest are impersonation risks.
RESERVED_USERNAMES = frozenset(
    {
        "admin",
        "administrator",
        "api",
        "help",
        "join",
        "me",
        "null",
        "root",
        "settings",
        "support",
        "system",
        "undefined",
        "user",
        "vocably",
    }
)


def is_valid_username(username: str) -> bool:
    """Whether ``username`` is well-formed and not reserved.

    Reserved names are *invalid*, not merely taken: reporting them as
    unavailable is what keeps the answer identical whether or not anyone holds
    them, so the endpoint cannot be used to probe which reserved names exist.
    """
    return bool(USERNAME_PATTERN.match(username)) and username not in RESERVED_USERNAMES


def normalize(username: str) -> str:
    """Fold to the stored form. Handles are stored already-lowercased."""
    return username.strip().lower()


def search_prefix(raw: str) -> str:
    """The searchable prefix in ``raw``, or ``""`` when there isn't one.

    Tolerant of how a handle is written down — a leading ``@`` is how people
    quote one to each other — and strict about everything else: anything that
    could not begin a handle cannot begin a match either, so it is answered with
    no results rather than a query.
    """
    candidate = normalize(raw).lstrip("@")
    if len(candidate) < USERNAME_SEARCH_MIN_LENGTH or len(candidate) > USERNAME_MAX_LENGTH:
        return ""
    return candidate if _PREFIX_PATTERN.match(candidate) else ""


def slugify(raw: str) -> str:
    """Derive a candidate handle from a display name, the client's way.

    Lowercase, drop anything outside ``[a-z0-9_]``, trim leading non-letters,
    cap at 20. Returns ``""`` when nothing usable survives — the caller decides
    what to fall back to, because the answer differs between onboarding (ask
    again) and backfill (generate one).
    """
    slug = re.sub(r"[^a-z0-9_]", "", raw.strip().lower())
    slug = re.sub(r"^[^a-z]+", "", slug)[:USERNAME_MAX_LENGTH]
    return slug if len(slug) >= USERNAME_MIN_LENGTH else ""


def fallback_username(user_id: UUID) -> str:
    """A guaranteed-valid handle for someone whose name yields nothing usable.

    Deterministic in the user id so a re-run of the backfill proposes the same
    handle rather than a second one.
    """
    return f"user_{user_id.hex[:8]}"
