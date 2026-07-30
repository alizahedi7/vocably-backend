"""Port: persistence contract for :class:`~app.domain.entities.review_event.ReviewEvent`.

Deliberately narrow. Every method here has a caller today; the analytics reads
this log will eventually serve (retention curves, per-deck difficulty) belong on
rolled-up aggregates, not on ad-hoc scans of the raw event table, and should be
added as their own port when that layer exists.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from app.domain.entities.review_event import ReviewEvent


class ReviewEventRepository(ABC):
    @abstractmethod
    async def add(self, event: ReviewEvent) -> None:
        """Append one review to the log.

        Returns nothing on purpose: the generated id is not read back, which
        keeps the write a single unidirectional INSERT rather than an
        INSERT..RETURNING plus an ORM refresh on the hottest path in the app.
        """

    @abstractmethod
    async def list_for_word(self, word_id: UUID, *, limit: int = 100) -> Sequence[ReviewEvent]:
        """Return a card's reviews, most recent first."""
