"""Port: the experience ledger and the counter beside it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from uuid import UUID

from app.domain.entities.xp import XpAction


class XpRepository(ABC):
    @abstractmethod
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
        """Append a ledger row and add its points to the counter.

        Returns the points actually awarded — zero when ``once_per_day`` is set
        and the learner has already been paid for that action today. That check
        is a unique index rather than a read-then-write, so two sessions
        finishing at once cannot both collect the daily goal.
        """
