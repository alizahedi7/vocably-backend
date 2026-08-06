"""A card as one learner sees it: shared content plus that learner's own state.

A read model, not an aggregate — it owns no behaviour and is never persisted.
It exists so the wire contract survives the split: ``WordOut`` still serialises
one flat object with ``box``, ``due_at``, ``review_count`` and
``last_reviewed_at`` on it, even though those columns now live in a different
table from ``term`` and ``meaning``. An Android build from before the split
cannot tell that anything moved.

The properties are written out rather than delegated through ``__getattr__``
precisely because they *are* that contract: adding one should be a deliberate
edit, and ``from_attributes`` needs them to be real attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.word import Word
from app.domain.entities.word_progress import WordProgress
from app.domain.enums import LeitnerBox, ReviewGrade


@dataclass(frozen=True, slots=True)
class StudiedWord:
    word: Word
    #: Never ``None``: a learner who has not studied the card gets
    #: :meth:`WordProgress.unstudied`, so every read has a full set of values
    #: without a row having to exist.
    progress: WordProgress

    # ── the card: shared by everyone in the deck ──
    @property
    def id(self) -> UUID:
        return self.word.id

    @property
    def deck_id(self) -> UUID:
        return self.word.deck_id

    @property
    def created_by_user_id(self) -> UUID | None:
        return self.word.created_by_user_id

    @property
    def term(self) -> str:
        return self.word.term

    @property
    def meaning(self) -> str:
        return self.word.meaning

    @property
    def definition(self) -> str | None:
        return self.word.definition

    @property
    def example(self) -> str | None:
        return self.word.example

    @property
    def sense_label(self) -> str | None:
        return self.word.sense_label

    @property
    def created_at(self) -> datetime:
        return self.word.created_at

    # ── this learner's study state ──
    @property
    def user_id(self) -> UUID:
        return self.progress.user_id

    @property
    def box(self) -> LeitnerBox:
        return self.progress.box

    @property
    def due_at(self) -> datetime:
        return self.progress.due_at

    @property
    def review_count(self) -> int:
        return self.progress.review_count

    @property
    def last_reviewed_at(self) -> datetime | None:
        return self.progress.last_reviewed_at

    @property
    def last_grade(self) -> ReviewGrade | None:
        return self.progress.last_grade

    def is_due(self, now: datetime) -> bool:
        return self.progress.is_due(now)
