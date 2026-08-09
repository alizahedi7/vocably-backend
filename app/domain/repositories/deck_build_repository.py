"""Port: persistence for deck build jobs and their items.

The item table is the pipeline's coordination primitive, so three of the methods
below are not ordinary CRUD and their contracts matter:

``claim_batch``
    A **conditional** UPDATE, committed before any provider call. Zero rows
    updated means another worker holds the item. This is what makes
    ``task_acks_late`` redelivery safe and what lets several workers share a job
    without a lock.

``bump_counters``
    Increments **in SQL**. A read-modify-write would lose counts whenever two
    items finish together — the same bug ``record_grade`` already avoids.

``reset_failed``
    Retry is "put the failed rows back", not "plan the job again". Attempts are
    cleared, everything else is left, and the items that succeeded are untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.domain.entities.deck_build import DeckBuildItem, DeckBuildJob
from app.domain.enums import DeckBuildItemState, DeckBuildState


class DeckBuildRepository(ABC):
    # ── Jobs ──────────────────────────────────────────────────
    @abstractmethod
    async def create_job(self, job: DeckBuildJob, items: list[DeckBuildItem]) -> DeckBuildJob:
        """Write the job and its whole plan in one transaction.

        All-or-nothing on purpose: a job whose plan is half-written would build a
        deck missing words nobody could name, and the template is right there to
        plan again from.
        """

    @abstractmethod
    async def get_job(self, job_id: UUID) -> DeckBuildJob | None: ...

    @abstractmethod
    async def list_jobs(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[DeckBuildJob], int]: ...

    @abstractmethod
    async def active_job_for(self, template_slug: str) -> DeckBuildJob | None:
        """A planned or generating job for this template, if one exists.

        Guards against two concurrent builds of one template, which would race
        for the same items and double the deck.
        """

    @abstractmethod
    async def latest_job_for(self, template_slug: str) -> DeckBuildJob | None:
        """The most recent job for this template, whatever state it reached.

        Unlike :meth:`active_job_for` this finds finished builds too, which is
        the point: it answers "which deck did this template become?" for a deck
        that was built months ago and has been published ever since.
        """

    @abstractmethod
    async def set_job_state(
        self,
        job_id: UUID,
        state: DeckBuildState,
        *,
        last_error: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> None: ...

    @abstractmethod
    async def attach_deck(self, job_id: UUID, deck_id: UUID) -> None: ...

    @abstractmethod
    async def bump_counters(self, job_id: UUID, **deltas: int) -> None:
        """Add to the job's counters in SQL. Unknown keys are a programming error."""

    # ── Items ─────────────────────────────────────────────────
    @abstractmethod
    async def claim_batch(
        self,
        job_id: UUID,
        *,
        limit: int,
        now: datetime,
        reclaim_before: datetime,
    ) -> list[DeckBuildItem]:
        """Take up to ``limit`` items and mark them ``RESOLVING``.

        Eligible: ``PENDING`` whose ``next_attempt_at`` has passed (or is NULL),
        and ``RESOLVING`` claimed before ``reclaim_before`` — the latter being a
        worker that died holding the row. Ordered by ``position`` so a build
        progresses through the book rather than through a hash.

        Implementations must commit the claim before returning: the caller is
        about to spend seconds in a provider call with no transaction open.
        """

    @abstractmethod
    async def get_item(self, item_id: UUID) -> DeckBuildItem | None: ...

    @abstractmethod
    async def save_item(self, item: DeckBuildItem) -> None:
        """Persist an item's decided state — the terminal write for one word."""

    @abstractmethod
    async def list_items(
        self,
        job_id: UUID,
        *,
        states: tuple[DeckBuildItemState, ...] = (),
        needs_attention: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DeckBuildItem], int]:
        """Items of a job, optionally filtered.

        ``needs_attention`` is the review queue: failed items, items flagged for
        review, and items whose sense was picked by a strategy that guesses. It
        is a filter rather than a separate method because the admin screen wants
        it alongside the ordinary listing, paginated the same way.
        """

    @abstractmethod
    async def count_by_state(self, job_id: UUID) -> dict[DeckBuildItemState, int]: ...

    @abstractmethod
    async def has_open_items(self, job_id: UUID) -> bool:
        """Whether any item is still pending or claimed — the driver's loop test."""

    @abstractmethod
    async def reset_failed(self, job_id: UUID) -> int:
        """Return failed items to ``PENDING`` with attempts cleared."""
