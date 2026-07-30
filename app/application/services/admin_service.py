"""Admin dashboard use cases: read-only platform-wide analytics.

Every method here fans out to :class:`AdminRepository` aggregations; nothing
mutates state. Access is gated at the API layer by an admin-only dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.dto import (
    AdminCacheAliasRow,
    AdminCacheEntryRow,
    AdminCacheOverview,
    AdminDeckRow,
    AdminOverview,
    AdminUserRow,
    AdminWordRow,
    AuthMethodCount,
    DailyCount,
)
from app.application.ports.admin_repository import AdminRepository
from app.core.exceptions import NotFoundError

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

    async def cache_overview(self) -> AdminCacheOverview:
        return await self._admin.cache_overview()

    async def cache_entry(self, entry_id: UUID) -> AdminCacheEntryRow:
        entry = await self._admin.get_cache_entry(entry_id)
        if entry is None:
            raise NotFoundError("Cache entry not found.")
        return entry

    async def cache_entries(
        self, limit: int, offset: int, search: str | None
    ) -> tuple[list[AdminCacheEntryRow], int]:
        return await self._admin.list_cache_entries(limit, offset, search)

    async def cache_aliases(
        self, entry_id: UUID, limit: int, offset: int
    ) -> tuple[list[AdminCacheAliasRow], int]:
        return await self._admin.list_cache_aliases(entry_id, limit, offset)
