"""A deck build: the plan for turning a template into a deck, one word at a time.

The plan is rows, not memory. That single decision is what makes the pipeline
resumable, idempotent and observable at once:

* **Resumable** — a worker killed at word 736 of 1000 leaves 735 ``DONE`` rows
  behind. Re-running processes the remainder and nothing else.
* **Idempotent** — building is a *state transition on an existing row*, never an
  insert, so a redelivered Celery message (``task_acks_late`` guarantees these)
  finds the work already done and skips it.
* **Observable** — progress is a ``GROUP BY state``, not a log scrape.

The counters on :class:`DeckBuildJob` are the honest measure of whether the
lexicon is doing its job: ``lexemes_reused`` against ``lexemes_generated`` is the
reuse ratio, and on a second exam deck built after the first it should be high.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.enums import DeckBuildItemState, DeckBuildState, SenseSelection

#: How many times one word is attempted before the item is left failed. Small on
#: purpose: three provider failures on one term is a term-shaped problem, not a
#: weather-shaped one, and the job must not spend an afternoon proving it.
MAX_ITEM_ATTEMPTS = 3


@dataclass(slots=True)
class SenseHint:
    """What the template says about *which* sense this deck wants.

    All three fields are optional and they compose: a pin without a hint is an
    exact instruction, a hint without a pin is a preference, and neither is the
    ordinary case for the several hundred words where any sense will do.
    """

    #: Part of speech the deck wants ("verb"). Half of an explicit pin.
    part_of_speech: str = ""
    #: Context label the deck wants ("Management"). The other half.
    context: str = ""
    #: Free text disambiguating the sense in the source's own words, e.g.
    #: "to be in charge of a company". Scored against candidate senses; never
    #: copied onto a card, and never a place to paste a source's definition.
    gloss: str = ""

    @property
    def is_pinned(self) -> bool:
        """Whether this names one sense outright rather than describing it."""
        return bool(self.part_of_speech.strip() and self.context.strip())

    @property
    def is_empty(self) -> bool:
        return not (self.part_of_speech or self.context or self.gloss)


@dataclass(slots=True)
class DeckBuildJob:
    """One run of one template."""

    id: UUID = field(default_factory=uuid4)
    template_slug: str = ""
    template_version: str = ""
    #: sha256 of the template files as read. Records exactly what was built, so
    #: a later diff of the template shows precisely what a rebuild would change.
    template_hash: str = ""
    deck_id: UUID | None = None
    state: DeckBuildState = DeckBuildState.PLANNED
    #: Pinned for the whole run at plan time. A deploy that bumps
    #: ``PROMPT_VERSION`` at word 300 of 504 must not leave the second half of
    #: the deck written by a different prompt than the first.
    content_version: int = 0
    native_language: str = "Persian"
    register: str = "adult"
    #: The deck's category, and the sense-selection ladder, copied from the
    #: template at plan time. Copied rather than re-read: the plan is already in
    #: the database and must stay buildable if the file is edited or moved
    #: mid-build, and a build that silently changed strategy halfway would be
    #: impossible to review.
    category: str = "general"
    strategies: tuple[SenseSelection, ...] = ()
    items_total: int = 0
    items_done: int = 0
    items_failed: int = 0
    #: Words answered from the lexicon with no provider call at all.
    lexemes_reused: int = 0
    #: Words the pipeline had to generate from scratch.
    lexemes_generated: int = 0
    #: Words that existed but lacked the sense this deck needed.
    senses_enriched: int = 0
    #: Provider calls this job is responsible for. The cost estimate's numerator.
    ai_calls: int = 0
    created_by_user_id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def items_pending(self) -> int:
        return max(self.items_total - self.items_done - self.items_failed, 0)

    @property
    def progress_pct(self) -> int:
        if self.items_total <= 0:
            return 0
        settled = self.items_done + self.items_failed
        return min(round(settled * 100 / self.items_total), 100)

    @property
    def reuse_pct(self) -> int:
        """Share of resolved words that cost no generation. The headline metric."""
        resolved = self.lexemes_reused + self.lexemes_generated
        if resolved <= 0:
            return 0
        return round(self.lexemes_reused * 100 / resolved)


@dataclass(slots=True)
class DeckBuildItem:
    """One word of one build, and everything decided about it."""

    id: UUID = field(default_factory=uuid4)
    job_id: UUID = field(default_factory=uuid4)
    #: The template's order, and the deck's. Authoritative and never recomputed:
    #: for a reference deck the sequence *is* the product.
    position: int = 0
    unit_label: str = ""
    unit_position: int = 0
    #: Exactly as the template wrote it, for diffing against the source.
    source_term: str = ""
    #: ``normalize_lookup_input`` applied — what the lexicon is asked for.
    normalized: str = ""
    hint: SenseHint = field(default_factory=SenseHint)
    state: DeckBuildItemState = DeckBuildItemState.PENDING
    lexeme_id: UUID | None = None
    sense_id: UUID | None = None
    word_id: UUID | None = None
    selection: SenseSelection | None = None
    #: How well the winning sense matched, 0..1. Only meaningful for the
    #: strategies that score; the review queue sorts on it.
    selection_score: float | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    #: At most one enrichment per item, ever — the guard that stops a word the
    #: model cannot satisfy from being asked about on every retry.
    enriched: bool = False
    claimed_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_exhausted(self) -> bool:
        return self.attempts >= MAX_ITEM_ATTEMPTS

    def backoff_seconds(self) -> int:
        """Delay before the next attempt: 2 min, 4 min, 8 min.

        Computed on the row rather than by Celery's retry machinery, because the
        unit of failure here is an *item* and the task carries a whole batch —
        retrying the task would re-punish the words that succeeded.
        """
        return int(120 * (2 ** max(self.attempts - 1, 0)))
