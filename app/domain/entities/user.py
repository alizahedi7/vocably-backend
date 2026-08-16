"""User domain entity — a pure dataclass, independent of persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from app.domain.enums import AgeRange, AuthMethod
from app.domain.services.streak import Streak


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
    #: Experience points; the client derives level and progress from it.
    xp: int = 0
    #: The last day anything at all was reviewed. Kept because it is on
    #: ``UserOut`` and installed Android builds parse it — and because it is a
    #: genuinely different question from the two dates below, which are about
    #: the *streak*: a learner can review one card without banking the day.
    last_studied_on: date | None = None
    #: The last day that counted towards the streak — banked or rested.
    streak_last_day: date | None = None
    #: The last day the daily goal was met. The once-a-day lock; see
    #: :mod:`app.domain.services.streak`.
    streak_banked_on: date | None = None

    onboarded: bool = False

    # Administration
    is_admin: bool = False
    last_login_at: datetime | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def display_name(self) -> str:
        return self.name.strip() or "there"

    @property
    def streak_state(self) -> Streak:
        """The three streak columns as the value the domain rule operates on."""
        return Streak(
            days=self.streak,
            last_day=self.streak_last_day,
            banked_on=self.streak_banked_on,
        )

    def note_study_day(self, today: date) -> bool:
        """Record that *something* was reviewed on ``today``. True if it changed.

        Deliberately not the streak. Reviewing one card is what this marks;
        whether the day is worth a streak day is a separate question, answered
        from the activity rollup by ``StudyService`` — an endpoint that banks a
        streak on the strength of one grade is an endpoint that hands out a
        streak on request.
        """
        if self.last_studied_on == today:
            return False
        self.last_studied_on = today
        return True
