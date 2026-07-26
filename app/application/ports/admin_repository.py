"""Port: read-only aggregation queries powering the admin dashboard.

Unlike the domain repositories (which are scoped to a single owner), these span
every user and return application-level read models, so the contract lives with
the use case that needs it rather than in the domain. Implementations never
mutate state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

from app.application.dto import (
    AdminDeckRow,
    AdminUserRow,
    AdminWordRow,
    AuthMethodCount,
    DailyCount,
)


class AdminRepository(ABC):
    @abstractmethod
    async def count_users(self) -> int: ...

    @abstractmethod
    async def count_decks(self) -> int: ...

    @abstractmethod
    async def count_words(self) -> int: ...

    @abstractmethod
    async def count_onboarded_users(self) -> int: ...

    @abstractmethod
    async def count_users_registered_since(self, since: datetime) -> int: ...

    @abstractmethod
    async def count_active_users_since(self, since: datetime) -> int:
        """Users whose ``last_login_at`` is at or after ``since``."""

    @abstractmethod
    async def registrations_by_day(self, since: date) -> list[DailyCount]:
        """Sign-up counts grouped by UTC calendar day, from ``since`` onward."""

    @abstractmethod
    async def auth_method_counts(self) -> list[AuthMethodCount]: ...

    @abstractmethod
    async def list_user_rows(self) -> list[AdminUserRow]:
        """Every user with their deck and word counts, newest registration first."""

    @abstractmethod
    async def list_deck_rows(self) -> list[AdminDeckRow]:
        """Every deck with its owner's name and word count."""

    @abstractmethod
    async def list_word_rows(self) -> list[AdminWordRow]:
        """Every word with its deck ("category") and owner names."""
