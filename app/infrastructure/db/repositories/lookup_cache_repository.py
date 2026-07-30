"""SQLAlchemy implementation of :class:`LookupCacheRepository`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.ai_service import LookupResult, LookupStatus
from app.application.ports.lookup_cache import LookupCacheKey, LookupCacheRepository
from app.core.logging import get_logger
from app.infrastructure.db import lookup_cache_payload
from app.infrastructure.db.models.ai_lookup import AILookupAliasModel, AILookupEntryModel

logger = get_logger("vocably.ai.cache")


class SqlAlchemyLookupCacheRepository(LookupCacheRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: LookupCacheKey) -> LookupResult | None:
        if not key.is_aliasable:
            # Long input is never aliased, so it can never hit. Skip the query
            # rather than paying for a lookup we know the answer to.
            return None

        stmt = (
            select(AILookupAliasModel, AILookupEntryModel)
            .outerjoin(
                AILookupEntryModel,
                AILookupAliasModel.entry_id == AILookupEntryModel.id,
            )
            .where(AILookupAliasModel.alias_hash == key.digest())
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None

        alias: AILookupAliasModel = row[0]
        entry: AILookupEntryModel | None = row[1]

        if alias.expires_at is not None and alias.expires_at <= datetime.now(UTC):
            return None

        try:
            status = LookupStatus(alias.status)
        except ValueError:
            # A status this deploy no longer knows — treat as a miss and let the
            # provider answer, rather than handing the client a value its
            # contract does not cover.
            logger.warning("Discarding cached lookup with unknown status %r", alias.status)
            return None

        if status is LookupStatus.UNSUPPORTED:
            return LookupResult(
                term=alias.resolved_term,
                suggestions=[],
                status=status,
                notice=alias.notice,
            )

        if entry is None:
            return None
        suggestions = lookup_cache_payload.decode(entry.payload)
        if not suggestions:
            # Unreadable at this schema version, or an entry that somehow holds
            # no senses. Either way the learner must not see an empty deck.
            return None

        await self._record_hit(entry.id)

        return LookupResult(
            term=alias.resolved_term,
            suggestions=suggestions,
            status=status,
            notice=alias.notice,
        )

    async def _record_hit(self, entry_id: UUID) -> None:
        """Best-effort: a failed counter update must never turn a hit into a
        miss, so this is never allowed to raise into the caller.
        """
        try:
            stmt = (
                update(AILookupEntryModel)
                .where(AILookupEntryModel.id == entry_id)
                .values(
                    hit_count=AILookupEntryModel.hit_count + 1,
                    last_accessed_at=datetime.now(UTC),
                )
            )
            await self._session.execute(stmt)
        except Exception:
            logger.warning("Failed to record lookup cache hit", exc_info=True)

    async def put(
        self,
        key: LookupCacheKey,
        result: LookupResult,
        *,
        alias_ttl_seconds: int | None = None,
        provider: str = "",
        model: str = "",
    ) -> None:
        entry_id: UUID | None = None
        if result.suggestions:
            entry_id = await self._ensure_entry(
                key.for_term(result.term),
                result,
                provider=provider,
                model=model,
            )

        if not key.is_aliasable:
            # The entry is still worth having: the next learner who looks up the
            # word this sentence contained gets it for free.
            return

        expires_at = (
            datetime.now(UTC) + timedelta(seconds=alias_ttl_seconds)
            if alias_ttl_seconds is not None
            else None
        )
        await self._ensure_alias(key, result, entry_id=entry_id, expires_at=expires_at)

    # ── Writes ────────────────────────────────────────────────

    async def _ensure_entry(
        self,
        entry_key: LookupCacheKey,
        result: LookupResult,
        *,
        provider: str,
        model: str,
    ) -> UUID | None:
        digest = entry_key.digest()
        if existing := await self._entry_id(digest):
            return existing

        entry = AILookupEntryModel(
            entry_hash=digest,
            term=result.term[:255],
            native_language=entry_key.native_language[:64],
            age_bucket=entry_key.age_bucket.value,
            prompt_version=entry_key.prompt_version,
            payload=lookup_cache_payload.encode(result.suggestions),
            provider=provider[:32],
            model=model[:128],
        )
        if await self._insert(entry):
            return entry.id
        # Another request stored the same term between our check and our insert.
        # That is the expected outcome of two learners looking up a new word at
        # once, not an error — adopt their row.
        return await self._entry_id(digest)

    async def _ensure_alias(
        self,
        key: LookupCacheKey,
        result: LookupResult,
        *,
        entry_id: UUID | None,
        expires_at: datetime | None,
    ) -> None:
        if await self._alias_exists(key.digest()):
            return
        alias = AILookupAliasModel(
            alias_hash=key.digest(),
            normalized_input=key.normalized_input[:255],
            native_language=key.native_language[:64],
            age_bucket=key.age_bucket.value,
            prompt_version=key.prompt_version,
            entry_id=entry_id,
            status=result.status.value,
            notice=result.notice,
            resolved_term=result.term[:255],
            expires_at=expires_at,
        )
        await self._insert(alias)

    async def _insert(self, model: AILookupAliasModel | AILookupEntryModel) -> bool:
        """Insert inside a savepoint; ``False`` if a concurrent writer won.

        A savepoint rather than ``ON CONFLICT`` so the same code runs on the
        Postgres of production and the SQLite of the default test suite, and so a
        losing race rolls back only this statement — the request's own
        transaction, which may already hold the learner's real work, survives.
        """
        try:
            async with self._session.begin_nested():
                self._session.add(model)
                await self._session.flush()
        except IntegrityError:
            return False
        return True

    # ── Reads ─────────────────────────────────────────────────

    async def _entry_id(self, digest: str) -> UUID | None:
        stmt = select(AILookupEntryModel.id).where(AILookupEntryModel.entry_hash == digest)
        return (await self._session.execute(stmt)).scalars().first()

    async def _alias_exists(self, digest: str) -> bool:
        stmt = select(AILookupAliasModel.id).where(AILookupAliasModel.alias_hash == digest)
        return (await self._session.execute(stmt)).scalars().first() is not None
