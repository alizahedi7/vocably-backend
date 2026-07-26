"""Admin dashboard use cases: read-only platform-wide analytics.

Every method here fans out to :class:`AdminRepository` aggregations; nothing
mutates state. Access is gated at the API layer by an admin-only dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.dto import (
    AdminDeckRow,
    AdminOverview,
    AdminUserRow,
    AdminWordRow,
    AuthMethodCount,
    DailyCount,
)
from app.application.ports.admin_repository import AdminRepository

#: Window used for "new" and "active" headline metrics.
_ACTIVITY_WINDOW = timedelta(days=7)


class AdminService:
    def __init__(self, admin: AdminRepository) -> None:
        self._admin = admin

    async def overview(self) -> AdminOverview:
        now = datetime.now(UTC)
        since = now - _ACTIVITY_WINDOW
        total_users = await self._admin.count_users()
        onboarded = await self._admin.count_onboarded_users()
        return AdminOverview(
            total_users=total_users,
            new_users_last_7d=await self._admin.count_users_registered_since(since),
            total_decks=await self._admin.count_decks(),
            total_words=await self._admin.count_words(),
            active_users_last_7d=await self._admin.count_active_users_since(since),
            onboarded_rate=(onboarded / total_users) if total_users else 0.0,
        )

    async def registrations(self, days: int) -> list[DailyCount]:
        """Sign-ups per day over the trailing ``days`` window, oldest day first.

        Days with no sign-ups are filled in as zero so the series is contiguous.
        """
        today = datetime.now(UTC).date()
        start = today - timedelta(days=days - 1)
        counts = {row.day: row.count for row in await self._admin.registrations_by_day(start)}
        return [
            DailyCount(day=day, count=counts.get(day, 0))
            for day in (start + timedelta(days=offset) for offset in range(days))
        ]

    async def auth_methods(self) -> list[AuthMethodCount]:
        return await self._admin.auth_method_counts()

    async def users(self) -> list[AdminUserRow]:
        return await self._admin.list_user_rows()

    async def categories(self) -> list[AdminDeckRow]:
        return await self._admin.list_deck_rows()

    async def words(self) -> list[AdminWordRow]:
        return await self._admin.list_word_rows()
