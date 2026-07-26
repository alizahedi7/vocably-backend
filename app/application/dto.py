"""Application-level DTOs returned by use-case services.

These are transport-agnostic (no Pydantic/FastAPI). The API layer maps them to response
schemas. Domain entities are returned directly where they suffice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.entities.deck import Deck
from app.domain.entities.user import User
from app.domain.entities.word import Word
from app.domain.enums import AuthMethod, LeitnerBox


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
    word_count: int
    due_count: int
    progress_pct: int  # 0..100, mean box normalised to the 5-box scale


@dataclass(frozen=True, slots=True)
class StudyOverview:
    """Data behind the home screen's 'Today's review' card and stats."""

    due_count: int
    total_count: int
    learned_count: int
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
    """A word with the deck ("category") and owner it belongs to."""

    word: Word
    deck_name: str
    owner_name: str
