"""SQLAlchemy implementation of :class:`FeedbackRepository`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.feedback import (
    AIFeedback,
    AIFeedbackReason,
    AIRating,
    ClientContext,
    ClientPlatform,
    FeedbackKind,
    FeedbackReport,
)
from app.domain.repositories.feedback_repository import (
    AIFeedbackTotals,
    AISenseScore,
    FeedbackRepository,
    LookupProvenance,
)
from app.infrastructure.db import lookup_cache_payload
from app.infrastructure.db.models.ai_lookup import AILookupEntryModel
from app.infrastructure.db.models.feedback import AIFeedbackModel, FeedbackReportModel


def _count_where(condition: Any) -> Any:
    """``COUNT(*) FILTER (WHERE …)``, spelled portably.

    ``FILTER`` is Postgres and modern SQLite only, and the test suite runs on
    whichever SQLite the interpreter was built against. ``SUM(CASE …)`` is the
    same query plan on both and works everywhere.
    """
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


class SqlAlchemyFeedbackRepository(FeedbackRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── written reports ──────────────────────────────────────
    async def add_report(self, report: FeedbackReport) -> FeedbackReport:
        model = FeedbackReportModel(
            id=report.id,
            # Written from the entity rather than left to ``server_default``:
            # ``now()`` is whole seconds on SQLite and transaction-start time on
            # Postgres, so two reports a moment apart tie — and the list is
            # ordered by this. A tie there falls through to a random UUID, which
            # is no order at all.
            created_at=report.created_at,
            user_id=report.user_id,
            kind=report.kind.value,
            message=report.message,
            app_version=report.context.app_version,
            platform=report.context.platform.value,
            os_version=report.context.os_version,
            locale=report.context.locale,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _report_to_entity(model)

    async def list_reports(
        self,
        *,
        limit: int,
        offset: int,
        kind: FeedbackKind | None = None,
    ) -> list[FeedbackReport]:
        stmt = select(FeedbackReportModel).order_by(
            FeedbackReportModel.created_at.desc(),
            # A tie-break the clock cannot provide: two reports written in the
            # same millisecond must not swap places between two pages.
            FeedbackReportModel.id.desc(),
        )
        if kind is not None:
            stmt = stmt.where(FeedbackReportModel.kind == kind.value)
        stmt = stmt.limit(limit).offset(offset)
        models = (await self._session.execute(stmt)).scalars().all()
        return [_report_to_entity(m) for m in models]

    async def count_reports(self, *, kind: FeedbackKind | None = None) -> int:
        stmt = select(func.count()).select_from(FeedbackReportModel)
        if kind is not None:
            stmt = stmt.where(FeedbackReportModel.kind == kind.value)
        return int((await self._session.execute(stmt)).scalar_one())

    # ── AI ratings ───────────────────────────────────────────
    async def upsert_ai(self, feedback: AIFeedback) -> AIFeedback:
        """Move this learner's verdict, or write their first one.

        A read-then-write rather than a dialect-specific ``ON CONFLICT``: the
        row is keyed by a three-column unique constraint whose conflict target
        differs between Postgres and the SQLite the suite runs on, and this
        endpoint is nowhere near hot enough to be worth two code paths.
        """
        existing = await self._find_ai(feedback.user_id, feedback.lookup_id, feedback.sense_index)
        if existing is None:
            model = AIFeedbackModel(
                id=feedback.id,
                user_id=feedback.user_id,
                lookup_id=feedback.lookup_id,
                sense_index=feedback.sense_index,
                rating=feedback.rating.value,
                reason=feedback.reason.value if feedback.reason else None,
                term=feedback.term,
                native_language=feedback.native_language,
                prompt_version=feedback.prompt_version,
                provider=feedback.provider,
                model=feedback.model,
            )
            self._session.add(model)
        else:
            model = existing
            model.rating = feedback.rating.value
            model.reason = feedback.reason.value if feedback.reason else None
            # Provenance is refreshed too: a second verdict may resolve an entry
            # the first one could not (it had not been cached yet), and a blank
            # term that can now be filled in should be.
            model.term = feedback.term or model.term
            model.native_language = feedback.native_language or model.native_language
            model.prompt_version = feedback.prompt_version or model.prompt_version
            model.provider = feedback.provider or model.provider
            model.model = feedback.model or model.model
            # ``onupdate`` only fires for a row the flush actually changes, and
            # re-tapping the same thumb with the same reason changes nothing —
            # which would leave "last rated" pointing at the first tap.
            model.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(model)
        return _ai_to_entity(model)

    async def delete_ai(self, user_id: UUID, lookup_id: str, sense_index: int) -> None:
        await self._session.execute(
            delete(AIFeedbackModel).where(
                AIFeedbackModel.user_id == user_id,
                AIFeedbackModel.lookup_id == lookup_id,
                AIFeedbackModel.sense_index == sense_index,
            )
        )

    async def ai_sense_scores(self, *, limit: int, offset: int) -> list[AISenseScore]:
        downs = _count_where(AIFeedbackModel.rating == AIRating.DOWN.value)
        stmt = (
            select(
                AIFeedbackModel.lookup_id,
                AIFeedbackModel.sense_index,
                # The provenance columns are per-row but constant within a
                # group, so any aggregate picks the same value. ``max`` is the
                # portable one, and it prefers a filled-in value over a blank —
                # which is what we want where one rating resolved the entry and
                # an earlier one could not.
                func.max(AIFeedbackModel.term).label("term"),
                _count_where(AIFeedbackModel.rating == AIRating.UP.value).label("ups"),
                downs.label("downs"),
                _count_where(AIFeedbackModel.reason == AIFeedbackReason.WRONG_MEANING.value).label(
                    "wrong_meaning"
                ),
                _count_where(AIFeedbackModel.reason == AIFeedbackReason.BAD_EXAMPLE.value).label(
                    "bad_example"
                ),
                _count_where(AIFeedbackModel.reason == AIFeedbackReason.WRONG_SENSE.value).label(
                    "wrong_sense"
                ),
                func.max(AIFeedbackModel.prompt_version).label("prompt_version"),
                func.max(AIFeedbackModel.provider).label("provider"),
                func.max(AIFeedbackModel.model).label("model"),
                func.max(AIFeedbackModel.updated_at).label("last_rated_at"),
            )
            .group_by(AIFeedbackModel.lookup_id, AIFeedbackModel.sense_index)
            # Worst first: the only reason to open this list is to find what to
            # fix. Recency breaks ties so a fresh complaint outranks an old one
            # with the same score, and the grouping key settles the rest so
            # paging is stable.
            .order_by(
                downs.desc(),
                func.max(AIFeedbackModel.updated_at).desc(),
                AIFeedbackModel.lookup_id.asc(),
                AIFeedbackModel.sense_index.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            AISenseScore(
                lookup_id=row.lookup_id,
                sense_index=int(row.sense_index),
                term=row.term or "",
                ups=int(row.ups),
                downs=int(row.downs),
                wrong_meaning=int(row.wrong_meaning),
                bad_example=int(row.bad_example),
                wrong_sense=int(row.wrong_sense),
                prompt_version=int(row.prompt_version or 0),
                provider=row.provider or "",
                model=row.model or "",
                last_rated_at=row.last_rated_at,
            )
            for row in rows
        ]

    async def ai_totals(self) -> AIFeedbackTotals:
        stmt = select(
            _count_where(AIFeedbackModel.rating == AIRating.UP.value).cast(Integer),
            _count_where(AIFeedbackModel.rating == AIRating.DOWN.value).cast(Integer),
        )
        ups, downs = (await self._session.execute(stmt)).one()
        # Counted over the grouping, not over the rows: one sense carries many
        # verdicts, so `ups + downs` says nothing about how long the list is.
        rated = (
            select(AIFeedbackModel.lookup_id, AIFeedbackModel.sense_index)
            .group_by(AIFeedbackModel.lookup_id, AIFeedbackModel.sense_index)
            .subquery()
        )
        senses = (await self._session.execute(select(func.count()).select_from(rated))).scalar_one()
        return AIFeedbackTotals(
            ups=int(ups or 0), downs=int(downs or 0), rated_senses=int(senses or 0)
        )

    async def lookup_provenance(self, lookup_id: str) -> LookupProvenance | None:
        stmt = select(AILookupEntryModel).where(AILookupEntryModel.entry_hash == lookup_id)
        entry = (await self._session.execute(stmt)).scalars().first()
        if entry is None:
            return None
        decoded = lookup_cache_payload.decode(entry.payload)
        return LookupProvenance(
            term=entry.term,
            native_language=entry.native_language,
            prompt_version=entry.prompt_version,
            provider=entry.provider,
            model=entry.model,
            # An unreadable payload is a schema version we no longer parse. The
            # entry is still the right one — the term and the model are what the
            # verdict needs — so only the sense-index check goes unanswered.
            sense_count=len(decoded.suggestions) if decoded else 0,
        )

    async def _find_ai(
        self, user_id: UUID | None, lookup_id: str, sense_index: int
    ) -> AIFeedbackModel | None:
        stmt = select(AIFeedbackModel).where(
            AIFeedbackModel.user_id == user_id,
            AIFeedbackModel.lookup_id == lookup_id,
            AIFeedbackModel.sense_index == sense_index,
        )
        return (await self._session.execute(stmt)).scalars().first()


def _report_to_entity(model: FeedbackReportModel) -> FeedbackReport:
    return FeedbackReport(
        id=model.id,
        user_id=model.user_id,
        kind=FeedbackKind.parse(model.kind),
        message=model.message,
        context=ClientContext(
            app_version=model.app_version,
            platform=ClientPlatform.parse(model.platform),
            os_version=model.os_version,
            locale=model.locale,
        ),
        created_at=model.created_at,
    )


def _ai_to_entity(model: AIFeedbackModel) -> AIFeedback:
    return AIFeedback(
        id=model.id,
        user_id=model.user_id,
        lookup_id=model.lookup_id,
        sense_index=model.sense_index,
        rating=AIRating.parse(model.rating),
        reason=AIFeedbackReason.parse(model.reason),
        term=model.term,
        native_language=model.native_language,
        prompt_version=model.prompt_version,
        provider=model.provider,
        model=model.model,
        created_at=model.created_at,
    )
