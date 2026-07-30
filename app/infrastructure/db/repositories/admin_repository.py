"""SQLAlchemy implementation of :class:`AdminRepository`.

All methods are read-only aggregations spanning every user. They deliberately
use ``func.count`` / grouped queries rather than loading rows into memory so the
dashboard stays cheap as the user base grows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto import (
    AdminCacheAliasRow,
    AdminCacheEntryRow,
    AdminCacheOverview,
    AdminDeckRow,
    AdminUserRow,
    AdminWordRow,
    AuthMethodCount,
    DailyCount,
)
from app.application.ports.admin_repository import AdminRepository
from app.domain.enums import AuthMethod
from app.infrastructure.ai.prompts import PROMPT_VERSION
from app.infrastructure.db import mappers
from app.infrastructure.db.models.ai_lookup import AILookupAliasModel, AILookupEntryModel
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel


class SqlAlchemyAdminRepository(AdminRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scalar_int(self, stmt: Select[tuple[int]]) -> int:
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_users(self) -> int:
        return await self._scalar_int(select(func.count()).select_from(UserModel))

    async def count_decks(self) -> int:
        return await self._scalar_int(select(func.count()).select_from(DeckModel))

    async def count_words(self) -> int:
        return await self._scalar_int(select(func.count()).select_from(WordModel))

    async def count_onboarded_users(self) -> int:
        stmt = select(func.count()).select_from(UserModel).where(UserModel.onboarded.is_(True))
        return await self._scalar_int(stmt)

    async def count_users_registered_since(self, since: datetime) -> int:
        stmt = select(func.count()).select_from(UserModel).where(UserModel.created_at >= since)
        return await self._scalar_int(stmt)

    async def count_active_users_since(self, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(UserModel)
            .where(UserModel.last_login_at.is_not(None), UserModel.last_login_at >= since)
        )
        return await self._scalar_int(stmt)

    async def registrations_by_day(self, since: date) -> list[DailyCount]:
        # Bucket by UTC calendar day in Python rather than SQL: casting a timestamp
        # to a date is dialect-specific (on SQLite ``CAST(... AS DATE)`` has numeric
        # affinity and silently mangles the value), and the row set here is small.
        since_dt = datetime(since.year, since.month, since.day, tzinfo=UTC)
        stmt = select(UserModel.created_at).where(UserModel.created_at >= since_dt)
        created_ats = (await self._session.execute(stmt)).scalars().all()

        counts: dict[date, int] = {}
        for created_at in created_ats:
            day = created_at.date()
            counts[day] = counts.get(day, 0) + 1
        return [DailyCount(day=day, count=count) for day, count in sorted(counts.items())]

    async def auth_method_counts(self) -> list[AuthMethodCount]:
        stmt = select(UserModel.auth_method, func.count()).group_by(UserModel.auth_method)
        rows = (await self._session.execute(stmt)).all()
        return [
            AuthMethodCount(method=AuthMethod(method), count=int(count)) for method, count in rows
        ]

    async def list_user_rows(self) -> list[AdminUserRow]:
        deck_counts = (
            select(DeckModel.user_id, func.count().label("n"))
            .group_by(DeckModel.user_id)
            .subquery()
        )
        word_counts = (
            select(WordModel.user_id, func.count().label("n"))
            .group_by(WordModel.user_id)
            .subquery()
        )
        stmt = (
            select(
                UserModel,
                func.coalesce(deck_counts.c.n, 0),
                func.coalesce(word_counts.c.n, 0),
            )
            .outerjoin(deck_counts, deck_counts.c.user_id == UserModel.id)
            .outerjoin(word_counts, word_counts.c.user_id == UserModel.id)
            .order_by(UserModel.created_at.desc(), UserModel.id.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            AdminUserRow(
                user=mappers.user_to_entity(user),
                deck_count=int(deck_count),
                word_count=int(word_count),
            )
            for user, deck_count, word_count in rows
        ]

    async def list_deck_rows(self) -> list[AdminDeckRow]:
        word_counts = (
            select(WordModel.deck_id, func.count().label("n"))
            .group_by(WordModel.deck_id)
            .subquery()
        )
        stmt = (
            select(DeckModel, UserModel.name, func.coalesce(word_counts.c.n, 0))
            .join(UserModel, UserModel.id == DeckModel.user_id)
            .outerjoin(word_counts, word_counts.c.deck_id == DeckModel.id)
            .order_by(DeckModel.created_at.desc(), DeckModel.id.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            AdminDeckRow(
                deck=mappers.deck_to_entity(deck),
                owner_name=owner_name,
                word_count=int(word_count),
            )
            for deck, owner_name, word_count in rows
        ]

    async def list_word_rows(self) -> list[AdminWordRow]:
        stmt = (
            select(WordModel, DeckModel.name, UserModel.name)
            .join(DeckModel, DeckModel.id == WordModel.deck_id)
            .join(UserModel, UserModel.id == WordModel.user_id)
            .order_by(WordModel.created_at.desc(), WordModel.id.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            AdminWordRow(
                word=mappers.word_to_entity(word),
                deck_name=deck_name,
                owner_name=owner_name,
            )
            for word, deck_name, owner_name in rows
        ]

    async def cache_overview(self) -> AdminCacheOverview:
        now = datetime.now(UTC)
        total_hits_stmt = select(func.coalesce(func.sum(AILookupEntryModel.hit_count), 0))
        total_hits = (await self._session.execute(total_hits_stmt)).scalar_one()
        stale_entry_count = await self._scalar_int(
            select(func.count())
            .select_from(AILookupEntryModel)
            .where(AILookupEntryModel.prompt_version != PROMPT_VERSION)
        )
        expired_alias_count = await self._scalar_int(
            select(func.count())
            .select_from(AILookupAliasModel)
            .where(
                AILookupAliasModel.expires_at.is_not(None),
                AILookupAliasModel.expires_at <= now,
            )
        )
        return AdminCacheOverview(
            total_entries=await self._scalar_int(
                select(func.count()).select_from(AILookupEntryModel)
            ),
            total_aliases=await self._scalar_int(
                select(func.count()).select_from(AILookupAliasModel)
            ),
            total_hits=int(total_hits),
            current_prompt_version=PROMPT_VERSION,
            stale_entry_count=stale_entry_count,
            expired_alias_count=expired_alias_count,
        )

    async def get_cache_entry(self, entry_id: UUID) -> AdminCacheEntryRow | None:
        alias_counts = (
            select(AILookupAliasModel.entry_id, func.count().label("n"))
            .where(AILookupAliasModel.entry_id.is_not(None))
            .group_by(AILookupAliasModel.entry_id)
            .subquery()
        )
        stmt = (
            select(AILookupEntryModel, func.coalesce(alias_counts.c.n, 0))
            .outerjoin(alias_counts, alias_counts.c.entry_id == AILookupEntryModel.id)
            .where(AILookupEntryModel.id == entry_id)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        entry, alias_count = row
        return AdminCacheEntryRow(
            id=entry.id,
            term=entry.term,
            native_language=entry.native_language,
            age_bucket=entry.age_bucket,
            prompt_version=entry.prompt_version,
            provider=entry.provider,
            model=entry.model,
            hit_count=entry.hit_count,
            alias_count=int(alias_count),
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            last_accessed_at=entry.last_accessed_at,
        )

    async def list_cache_entries(
        self, limit: int, offset: int, search: str | None
    ) -> tuple[list[AdminCacheEntryRow], int]:
        alias_counts = (
            select(AILookupAliasModel.entry_id, func.count().label("n"))
            .where(AILookupAliasModel.entry_id.is_not(None))
            .group_by(AILookupAliasModel.entry_id)
            .subquery()
        )

        filters = []
        if search:
            filters.append(AILookupEntryModel.term.ilike(f"%{search}%"))

        total = await self._scalar_int(
            select(func.count()).select_from(AILookupEntryModel).where(*filters)
        )

        stmt = (
            select(AILookupEntryModel, func.coalesce(alias_counts.c.n, 0))
            .outerjoin(alias_counts, alias_counts.c.entry_id == AILookupEntryModel.id)
            .where(*filters)
            .order_by(AILookupEntryModel.hit_count.desc(), AILookupEntryModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        entries = [
            AdminCacheEntryRow(
                id=entry.id,
                term=entry.term,
                native_language=entry.native_language,
                age_bucket=entry.age_bucket,
                prompt_version=entry.prompt_version,
                provider=entry.provider,
                model=entry.model,
                hit_count=entry.hit_count,
                alias_count=int(alias_count),
                created_at=entry.created_at,
                updated_at=entry.updated_at,
                last_accessed_at=entry.last_accessed_at,
            )
            for entry, alias_count in rows
        ]
        return entries, total

    async def list_cache_aliases(
        self, entry_id: UUID, limit: int, offset: int
    ) -> tuple[list[AdminCacheAliasRow], int]:
        total = await self._scalar_int(
            select(func.count())
            .select_from(AILookupAliasModel)
            .where(AILookupAliasModel.entry_id == entry_id)
        )
        stmt = (
            select(AILookupAliasModel)
            .where(AILookupAliasModel.entry_id == entry_id)
            .order_by(AILookupAliasModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        aliases = (await self._session.execute(stmt)).scalars().all()
        rows = [
            AdminCacheAliasRow(
                id=alias.id,
                normalized_input=alias.normalized_input,
                native_language=alias.native_language,
                age_bucket=alias.age_bucket,
                prompt_version=alias.prompt_version,
                status=alias.status,
                notice=alias.notice,
                resolved_term=alias.resolved_term,
                expires_at=alias.expires_at,
                created_at=alias.created_at,
            )
            for alias in aliases
        ]
        return rows, total
