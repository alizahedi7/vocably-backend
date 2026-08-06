"""User domain entity — a pure dataclass, independent of persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from app.domain.enums import AgeRange, AuthMethod


@dataclass(slots=True)
class User:
    id: UUID = field(default_factory=uuid4)

    # Identity — at least one of phone / google_sub is set depending on auth method.
    auth_method: AuthMethod = AuthMethod.PHONE
    phone: str | None = None
    email: str | None = None
    google_sub: str | None = None

    # Profile
    name: str = ""
    #: The handle other people address this person by. ``None`` for accounts
    #: created before handles existed and not yet backfilled.
    username: str | None = None
    age_range: AgeRange | None = None
    native_language: str = "English"
    app_language: str = "English"
    target_language: str | None = None
    proficiency: str | None = None
    study_time: str | None = None
    #: IANA name; ``None`` means UTC. See app/domain/services/calendar.py.
    timezone: str | None = None

    # Learning preferences
    interests: list[str] = field(default_factory=list)
    daily_goal: int = 10

    # Gamification
    streak: int = 0
    last_studied_on: date | None = None

    onboarded: bool = False

    # Administration
    is_admin: bool = False
    last_login_at: datetime | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def display_name(self) -> str:
        return self.name.strip() or "there"

    def register_study_day(self, today: date) -> None:
        """Update the study streak given that the user studied on ``today``.

        Same day → no change. Consecutive day → +1. Any gap → reset to 1.
        """
        last = self.last_studied_on
        if last == today:
            return
        if last is not None and (today - last).days == 1:
            self.streak += 1
        else:
            self.streak = 1
        self.last_studied_on = today
