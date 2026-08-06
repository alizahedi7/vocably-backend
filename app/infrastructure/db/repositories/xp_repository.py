"""SQLAlchemy implementation of :class:`XpRepository`."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.xp import XpAction
from app.domain.repositories.xp_repository import XpRepository
from app.infrastructure.db.dialects import upsert_insert
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.xp_event import XpEventModel


class SqlAlchemyXpRepository(XpRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def award(
        self,
        user_id: UUID,
        action: XpAction,
        *,
        occurred_at: datetime,
        day: date,
        once_per_day: bool = False,
        ref_type: str | None = None,
        ref_id: UUID | None = None,
    ) -> int:
        points = action.points
        insert = upsert_insert(self._session)(XpEventModel).values(
            id=uuid.uuid4(),
            user_id=user_id,
            action=action.value,
            points=points,
            occurred_at=occurred_at,
            day=day,
            ref_type=ref_type,
            ref_id=ref_id,
            created_at=occurred_at,
        )
        if once_per_day:
            # Inference has to name the partial index's predicate too, or
            # Postgres cannot tell which index the conflict is on.
            insert = insert.on_conflict_do_nothing(
                index_elements=["user_id", "action", "day"],
                index_where=text("action = 'daily_goal'"),
            )
        stmt = insert.returning(XpEventModel.id)

        awarded = (await self._session.execute(stmt)).scalar_one_or_none()
        if awarded is None:
            # Already collected today. Nothing written, nothing counted.
            return 0

        # The counter is incremented in SQL for the same reason the review
        # counters are: two awards landing together must not overwrite one
        # another, or the number the profile shows drifts from the ledger.
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(xp=UserModel.xp + points)
        )
        return points
