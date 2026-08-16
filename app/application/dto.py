"""Application-level DTOs returned by use-case services.

These are transport-agnostic (no Pydantic/FastAPI). The API layer maps them to response
schemas. Domain entities are returned directly where they suffice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.application.ports.ai_service import LookupResult
from app.domain.entities.deck import Deck
from app.domain.entities.user import User
from app.domain.entities.word import Word
from app.domain.enums import AuthMethod, DeckRole, LeitnerBox


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Result of a successful sign-in: tokens plus whether the user is new."""

    user: User
    tokens: TokenPair
    is_new_user: bool


@dataclass(frozen=True, slots=True)
class BoxCount:
    box: LeitnerBox
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class MemoryStrength:
    total: int
    distribution: list[BoxCount]


@dataclass(frozen=True, slots=True)
class DeckView:
    """A deck plus the aggregate stats the UI shows on each deck card."""

    deck: Deck
    #: Every card in the deck, started or not — its size, which is the same
    #: number for every member and is what the deck screen says out loud.
    word_count: int
    #: Of those, the ones in *this* learner's boxes. Equal to ``word_count``
    #: for every deck anyone built themselves; smaller for a self-paced deck
    #: they are working through at their own rate.
    started_count: int
    due_count: int
    #: 0..100, mean box normalised to the 5-box scale — over *started* cards.
    #: Measured against the whole deck it would read 0% for months and tell the
    #: learner their work counted for nothing.
    progress_pct: int
    #: This user's role in *this* deck. On the list it is what tells a deck the
    #: client may delete from one it may only leave.
    role: DeckRole = DeckRole.OWNER
    #: Whether this deck's words wait to be started by this member. The client
    #: shows the "add to my boxes" affordances on the strength of it.
    self_paced: bool = False


@dataclass(frozen=True, slots=True)
class StudyOverview:
    """Data behind the home screen's 'Today's review' card and stats."""

    due_count: int
    total_count: int
    learned_count: int
    #: Words in box 5 across every deck. Distinct from ``learned_count``, which
    #: counts boxes 4 and 5 — the mastery badges are a pure function of this
    #: one, which is why no badge table exists.
    mastered_count: int
    #: Cards answered today, in the learner's own timezone. The client keeps a
    #: local counter too and takes the larger of the two: a grade that never
    #: reached the server is still work the learner did.
    reviewed_today: int
    due_deck_count: int
    estimated_minutes: int
    streak: int
    memory_strength: MemoryStrength


# ── Admin dashboard read models ──────────────────────────────
@dataclass(frozen=True, slots=True)
class AdminOverview:
    """Headline KPIs for the admin dashboard's overview page."""

    total_users: int
    new_users_last_7d: int
    total_decks: int
    total_words: int
    active_users_last_7d: int
    onboarded_rate: float  # 0..1


@dataclass(frozen=True, slots=True)
class DailyCount:
    """One point in a daily time series (e.g. sign-ups per calendar day, UTC)."""

    day: date
    count: int


@dataclass(frozen=True, slots=True)
class AuthMethodCount:
    method: AuthMethod
    count: int


@dataclass(frozen=True, slots=True)
class AdminUserRow:
    """A user plus the per-user aggregates the admin users table shows."""

    user: User
    deck_count: int
    word_count: int


@dataclass(frozen=True, slots=True)
class AdminDeckRow:
    """A deck ("category") with its owner's name and word count."""

    deck: Deck
    owner_name: str
    word_count: int


@dataclass(frozen=True, slots=True)
class AdminWordRow:
    """A word with the deck ("category") and creator it belongs to."""

    word: Word
    deck_name: str
    owner_name: str
    #: The *creator's* Leitner box. Study state is per member, so a shared card
    #: has one box per learner and none of them is the card's; this is the one
    #: belonging to whoever added it. 1 when they have never studied it.
    box: int


@dataclass(frozen=True, slots=True)
class AdminCacheOverview:
    """Headline KPIs for the admin AI-cache monitoring page."""

    total_entries: int
    total_aliases: int
    total_hits: int
    current_prompt_version: int
    stale_entry_count: int
    """Entries keyed to a retired ``PROMPT_VERSION`` — a prompt bump orphaned them."""
    expired_alias_count: int
    """``unsupported`` aliases past their TTL, awaiting the (not-yet-built) sweep."""


@dataclass(frozen=True, slots=True)
class AdminCacheEntryRow:
    """A cached word: its provenance and how often it has been reused."""

    id: UUID
    term: str
    native_language: str
    age_bucket: str
    prompt_version: int
    provider: str
    model: str
    hit_count: int
    alias_count: int
    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdminCacheAliasRow:
    """One raw learner input that resolved to a cache entry (or to nothing)."""

    id: UUID
    normalized_input: str
    native_language: str
    age_bucket: str
    prompt_version: int
    status: str
    notice: str | None
    resolved_term: str
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LookupView:
    """A lookup's senses, plus the id under which they can be rated.

    ``LookupResult`` is the *port's* shape — what an AI adapter returns — and it
    knows nothing about caching. The id here is derived from the cache key for
    the resolved term (``LookupCacheKey.digest``), which is a fact the use case
    computes and no adapter should have to. Hence a wrapper rather than another
    field on ``LookupResult``: the adapters would all have to leave it blank.

    ``lookup_id`` is empty exactly when there are no senses to rate.
    """

    result: LookupResult
    lookup_id: str = ""
