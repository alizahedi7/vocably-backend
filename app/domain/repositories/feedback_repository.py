"""Port: persistence for learner feedback — written reports and AI ratings.

One port for both tables. They are stored separately (see
:mod:`app.domain.entities.feedback`) but they are written by one use case and
read by one screen, and splitting the port would mean two repositories injected
side by side into every caller for no seam anybody would ever use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.feedback import AIFeedback, FeedbackKind, FeedbackReport


@dataclass(frozen=True, slots=True)
class AISenseScore:
    """How one AI-written card back has been rated, across every learner.

    The unit is the *sense*, not the lookup: a word whose second card is wrong
    and whose first is fine is a fixable prompt problem, and rolling the two
    together into one score for "run" hides it.
    """

    lookup_id: str
    sense_index: int
    #: Denormalised at rating time; empty only where the cache entry could not
    #: be resolved. See :class:`AIFeedback.term`.
    term: str
    ups: int
    downs: int
    #: The three reasons, counted. All zero for a sense nobody explained, which
    #: is the ordinary case — the reason chips are optional by design.
    wrong_meaning: int
    bad_example: int
    wrong_sense: int
    prompt_version: int
    provider: str
    model: str
    last_rated_at: datetime | None


@dataclass(frozen=True, slots=True)
class AIFeedbackTotals:
    """The one-line answer: how the AI cards are doing overall."""

    ups: int
    downs: int

    @property
    def total(self) -> int:
        return self.ups + self.downs


@dataclass(frozen=True, slots=True)
class LookupProvenance:
    """What the shared lookup cache knows about the deck a rating is about.

    Read at rating time so a verdict can be stamped with the word it is about
    and the prompt and model that wrote it, and so a sense index that does not
    exist can be refused. Absent whenever the entry cannot be found, which is
    normal rather than exceptional — see
    :meth:`FeedbackRepository.lookup_provenance`.
    """

    term: str
    native_language: str
    prompt_version: int
    provider: str
    model: str
    #: How many card backs the deck actually held. Zero for a payload written by
    #: a schema version this deploy can no longer read, which is indistinguishable
    #: from "we do not know" and is treated the same way.
    sense_count: int


class FeedbackRepository(ABC):
    # ── written reports ──────────────────────────────────────
    @abstractmethod
    async def add_report(self, report: FeedbackReport) -> FeedbackReport:
        """Store one report. Never updated afterwards — a report is a statement."""

    @abstractmethod
    async def list_reports(
        self,
        *,
        limit: int,
        offset: int,
        kind: FeedbackKind | None = None,
    ) -> list[FeedbackReport]:
        """Newest first, which is the only order a triage queue is ever read in."""

    @abstractmethod
    async def count_reports(self, *, kind: FeedbackKind | None = None) -> int:
        """Total matching ``kind``, so the admin list can page without guessing."""

    # ── AI ratings ───────────────────────────────────────────
    @abstractmethod
    async def upsert_ai(self, feedback: AIFeedback) -> AIFeedback:
        """Record ``feedback``, replacing this learner's previous verdict on the
        same ``(lookup_id, sense_index)``.

        Implementations must tolerate a concurrent writer: the same learner
        double-tapping on a slow connection is normal, and the second write must
        win rather than raise. ``created_at`` stays at the first verdict; it is
        ``updated_at`` that moves.
        """

    @abstractmethod
    async def delete_ai(self, user_id: UUID, lookup_id: str, sense_index: int) -> None:
        """Withdraw this learner's verdict. A no-op when there was none."""

    @abstractmethod
    async def ai_sense_scores(self, *, limit: int, offset: int) -> list[AISenseScore]:
        """The rated senses, worst first.

        Ordered by downs descending, because the only reason to open this list
        is to find what to fix. Aggregated in SQL — a score over every rating
        ever left is not a thing to load into Python.
        """

    @abstractmethod
    async def ai_totals(self) -> AIFeedbackTotals: ...

    @abstractmethod
    async def lookup_provenance(self, lookup_id: str) -> LookupProvenance | None:
        """What the lookup cache holds for ``lookup_id``, or ``None``.

        Reaches into the shared lookup cache rather than going through
        :class:`~app.application.ports.lookup_cache.LookupCacheRepository`,
        whose contract is deliberately "a request in, a result out" and has no
        read-by-hash. This is a *write-side* read — everything needed to stamp a
        verdict — so it belongs with the write it serves.

        ``None`` is an ordinary answer, not an error. An entry is missing
        whenever the prompt version that produced it has been swept, the input
        was too long to be cached at all, or a deployment is running without the
        cache in front of the provider. A rating must still be accepted in every
        one of those cases: the learner tapped a thumb and nothing on their
        screen could explain a refusal.
        """
