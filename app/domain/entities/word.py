"""Word (flashcard) domain entity, including its spaced-repetition state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.enums import LeitnerBox


@dataclass(slots=True)
class Word:
    id: UUID = field(default_factory=uuid4)
    deck_id: UUID = field(default_factory=uuid4)
    user_id: UUID = field(default_factory=uuid4)

    # Content
    term: str = ""
    meaning: str = ""
    # Plain-language dictionary definition of the chosen sense — the "DEFINITION"
    # body of the card back. Filled in by AI Card Magic
    # (``MeaningSuggestion.definition``) or written by the learner.
    definition: str | None = None
    example: str | None = None
    # e.g. "verb · progress" or "my definition" — mirrors the design's senseLabel.
    sense_label: str | None = None

    # Spaced-repetition state
    box: LeitnerBox = LeitnerBox.NEW
    due_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    review_count: int = 0
    last_reviewed_at: datetime | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_due(self, now: datetime) -> bool:
        return self.due_at <= now
