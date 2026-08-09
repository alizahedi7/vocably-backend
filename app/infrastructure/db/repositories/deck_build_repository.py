"""SQLAlchemy implementation of :class:`DeckBuildRepository`."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.deck_build import DeckBuildItem, DeckBuildJob
from app.domain.enums import DeckBuildItemState, DeckBuildState, SenseSelection
from app.domain.repositories.deck_build_repository import DeckBuildRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.deck_build import DeckBuildItemModel, DeckBuildJobModel

#: Counter columns ``bump_counters`` will touch. An allow-list rather than
#: ``getattr``: a typo in a keyword would otherwise increment nothing, silently,
#: and the reuse ratio would quietly read zero forever.
_COUNTERS = frozenset(
    {
        "items_done",
        "items_failed",
        "lexemes_reused",
        "lexemes_generated",
        "senses_enriched",
        "ai_calls",
    }
)

#: States a job may hold while still owning its items.
_ACTIVE_STATES = (DeckBuildState.PLANNED.value, DeckBuildState.GENERATING.value)


class SqlAlchemyDeckBuildRepository(DeckBuildRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Jobs ──────────────────────────────────────────────────

    async def create_job(self, job: DeckBuildJob, items: list[DeckBuildItem]) -> DeckBuildJob:
        model = DeckBuildJobModel(
            id=job.id,
            template_slug=job.template_slug[:80],
            template_version=job.template_version[:32],
            template_hash=job.template_hash[:64],
            deck_id=job.deck_id,
            state=job.state.value,
            content_version=job.content_version,
            native_language=job.native_language[:64],
            register=job.register[:16],
            category=job.category[:32],
            strategies=",".join(s.value for s in job.strategies)[:120],
            items_total=len(items),
            created_by_user_id=job.created_by_user_id,
        )
        self._session.add(model)
        await self._session.flush()

        self._session.add_all(
            DeckBuildItemModel(
                id=item.id,
                job_id=model.id,
                position=item.position,
                unit_label=item.unit_label[:40],
                unit_position=item.unit_position,
                source_term=item.source_term[:255],
                normalized=item.normalized[:255],
                hint=mappers.hint_to_payload(item.hint),
                state=item.state.value,
            )
            for item in items
        )
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.deck_build_job_to_entity(model)

    async def get_job(self, job_id: UUID) -> DeckBuildJob | None:
        model = await self._session.get(DeckBuildJobModel, job_id)
        return mappers.deck_build_job_to_entity(model) if model else None

    async def list_jobs(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[DeckBuildJob], int]:
        total = int(
            (
                await self._session.execute(select(func.count()).select_from(DeckBuildJobModel))
            ).scalar_one()
        )
        stmt = (
            select(DeckBuildJobModel)
            .order_by(DeckBuildJobModel.created_at.desc(), DeckBuildJobModel.id.desc())
            .limit(limit)
            .offset(offset)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.deck_build_job_to_entity(m) for m in models], total

    async def active_job_for(self, template_slug: str) -> DeckBuildJob | None:
        stmt = (
            select(DeckBuildJobModel)
            .where(
                DeckBuildJobModel.template_slug == template_slug,
                DeckBuildJobModel.state.in_(_ACTIVE_STATES),
            )
            .order_by(DeckBuildJobModel.created_at.desc())
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return mappers.deck_build_job_to_entity(model) if model else None

    async def latest_job_for(self, template_slug: str) -> DeckBuildJob | None:
        stmt = (
            select(DeckBuildJobModel)
            .where(DeckBuildJobModel.template_slug == template_slug)
            .order_by(DeckBuildJobModel.created_at.desc(), DeckBuildJobModel.id.desc())
        )
        model = (await self._session.execute(stmt)).scalars().first()
        return mappers.deck_build_job_to_entity(model) if model else None

    async def set_job_state(
        self,
        job_id: UUID,
        state: DeckBuildState,
        *,
        last_error: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        values: dict[str, object] = {"state": state.value, "updated_at": datetime.now(UTC)}
        if last_error is not None:
            values["last_error"] = last_error[:2000]
        if started_at is not None:
            values["started_at"] = started_at
        if finished_at is not None:
            values["finished_at"] = finished_at
        await self._session.execute(
            update(DeckBuildJobModel).where(DeckBuildJobModel.id == job_id).values(**values)
        )

    async def attach_deck(self, job_id: UUID, deck_id: UUID) -> None:
        await self._session.execute(
            update(DeckBuildJobModel)
            .where(DeckBuildJobModel.id == job_id)
            .values(deck_id=deck_id, updated_at=datetime.now(UTC))
        )

    async def bump_counters(self, job_id: UUID, **deltas: int) -> None:
        unknown = set(deltas) - _COUNTERS
        if unknown:
            raise ValueError(f"unknown build counters: {sorted(unknown)}")
        values = {
            name: getattr(DeckBuildJobModel, name) + delta
            for name, delta in deltas.items()
            if delta
        }
        if not values:
            return
        values["updated_at"] = datetime.now(UTC)
        await self._session.execute(
            update(DeckBuildJobModel).where(DeckBuildJobModel.id == job_id).values(**values)
        )

    # ── Items ─────────────────────────────────────────────────

    async def claim_batch(
        self,
        job_id: UUID,
        *,
        limit: int,
        now: datetime,
        reclaim_before: datetime,
    ) -> list[DeckBuildItem]:
        eligible = or_(
            # Fresh work, or a retry whose backoff has elapsed.
            (DeckBuildItemModel.state == DeckBuildItemState.PENDING.value)
            & (
                DeckBuildItemModel.next_attempt_at.is_(None)
                | (DeckBuildItemModel.next_attempt_at <= now)
            ),
            # A row a dead worker is still nominally holding.
            (DeckBuildItemModel.state == DeckBuildItemState.RESOLVING.value)
            & (
                DeckBuildItemModel.claimed_at.is_(None)
                | (DeckBuildItemModel.claimed_at <= reclaim_before)
            ),
        )
        stmt = (
            select(DeckBuildItemModel.id)
            .where(DeckBuildItemModel.job_id == job_id, eligible)
            # Position order, so a build walks the book rather than a hash — and
            # so the cards it writes carry increasing timestamps, which is what
            # keeps an Explore copy in the source's sequence.
            .order_by(DeckBuildItemModel.position.asc())
            .limit(limit)
        )
        candidate_ids = list((await self._session.execute(stmt)).scalars().all())
        if not candidate_ids:
            return []

        claimed: list[UUID] = []
        for item_id in candidate_ids:
            # Re-assert the predicate in the UPDATE: between the SELECT above and
            # this statement another worker may have taken the row, and zero rows
            # updated is exactly how we find out.
            result = await self._session.execute(
                update(DeckBuildItemModel)
                .where(DeckBuildItemModel.id == item_id, eligible)
                .values(
                    state=DeckBuildItemState.RESOLVING.value,
                    claimed_at=now,
                    updated_at=now,
                )
            )
            if result.rowcount:  # type: ignore[attr-defined]
                claimed.append(item_id)
        if not claimed:
            return []

        # Commit the claim before returning: the caller is about to spend seconds
        # in a provider call, and holding a transaction open across that is how a
        # 500-word build exhausts the connection pool.
        await self._session.commit()

        rows = (
            (
                await self._session.execute(
                    select(DeckBuildItemModel)
                    .where(DeckBuildItemModel.id.in_(claimed))
                    .order_by(DeckBuildItemModel.position.asc())
                )
            )
            .scalars()
            .all()
        )
        return [mappers.deck_build_item_to_entity(row) for row in rows]

    async def get_item(self, item_id: UUID) -> DeckBuildItem | None:
        model = await self._session.get(DeckBuildItemModel, item_id)
        return mappers.deck_build_item_to_entity(model) if model else None

    async def save_item(self, item: DeckBuildItem) -> None:
        await self._session.execute(
            update(DeckBuildItemModel)
            .where(DeckBuildItemModel.id == item.id)
            .values(
                state=item.state.value,
                lexeme_id=item.lexeme_id,
                sense_id=item.sense_id,
                word_id=item.word_id,
                selection=item.selection.value if item.selection else None,
                selection_score=item.selection_score,
                attempts=item.attempts,
                next_attempt_at=item.next_attempt_at,
                last_error=item.last_error[:2000] if item.last_error else None,
                enriched=item.enriched,
                claimed_at=item.claimed_at,
                updated_at=datetime.now(UTC),
            )
        )

    async def list_items(
        self,
        job_id: UUID,
        *,
        states: tuple[DeckBuildItemState, ...] = (),
        needs_attention: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DeckBuildItem], int]:
        stmt = select(DeckBuildItemModel).where(DeckBuildItemModel.job_id == job_id)
        count_stmt = (
            select(func.count())
            .select_from(DeckBuildItemModel)
            .where(DeckBuildItemModel.job_id == job_id)
        )
        if states:
            predicate = DeckBuildItemModel.state.in_([s.value for s in states])
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)
        if needs_attention:
            # The review queue: what failed, what was flagged, and what a
            # guessing strategy chose. Everything else a human never has to see.
            attention = or_(
                DeckBuildItemModel.state.in_(
                    [DeckBuildItemState.FAILED.value, DeckBuildItemState.NEEDS_REVIEW.value]
                ),
                DeckBuildItemModel.selection.in_(
                    [SenseSelection.FIRST.value, SenseSelection.CATEGORY.value]
                ),
            )
            stmt = stmt.where(attention)
            count_stmt = count_stmt.where(attention)

        total = int((await self._session.execute(count_stmt)).scalar_one())
        stmt = stmt.order_by(DeckBuildItemModel.position.asc()).limit(limit).offset(offset)
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.deck_build_item_to_entity(m) for m in models], total

    async def count_by_state(self, job_id: UUID) -> dict[DeckBuildItemState, int]:
        stmt = (
            select(DeckBuildItemModel.state, func.count())
            .where(DeckBuildItemModel.job_id == job_id)
            .group_by(DeckBuildItemModel.state)
        )
        rows = (await self._session.execute(stmt)).all()
        return {DeckBuildItemState(row[0]): int(row[1]) for row in rows}

    async def has_open_items(self, job_id: UUID) -> bool:
        stmt = (
            select(DeckBuildItemModel.id)
            .where(
                DeckBuildItemModel.job_id == job_id,
                DeckBuildItemModel.state.in_(
                    [DeckBuildItemState.PENDING.value, DeckBuildItemState.RESOLVING.value]
                ),
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first() is not None

    async def reset_failed(self, job_id: UUID) -> int:
        result = await self._session.execute(
            update(DeckBuildItemModel)
            .where(
                DeckBuildItemModel.job_id == job_id,
                DeckBuildItemModel.state == DeckBuildItemState.FAILED.value,
            )
            .values(
                state=DeckBuildItemState.PENDING.value,
                attempts=0,
                next_attempt_at=None,
                claimed_at=None,
                last_error=None,
                updated_at=datetime.now(UTC),
            )
        )
        reset = int(result.rowcount or 0)  # type: ignore[attr-defined]
        if reset:
            # The job's failure count has to come back down with them, or the
            # dashboard reports failures that no longer exist.
            await self._session.execute(
                update(DeckBuildJobModel)
                .where(DeckBuildJobModel.id == job_id)
                .values(items_failed=DeckBuildJobModel.items_failed - reset)
            )
        return reset
