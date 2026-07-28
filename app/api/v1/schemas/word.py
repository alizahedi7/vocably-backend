"""Word request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import LeitnerBox


class WordCreateIn(BaseModel):
    deck_id: UUID
    term: str = Field(min_length=1, max_length=255)
    meaning: str = Field(min_length=1, max_length=2000)
    #: Dictionary definition of the sense being learned — the "DEFINITION" body
    #: of the card back (see ``docs/ai-card-magic-contract.md``). Optional: AI
    #: Card Magic fills it in, a hand-written card is valid without one, and
    #: clients older than the field simply never send it.
    definition: str | None = Field(default=None, max_length=2000)
    example: str | None = Field(default=None, max_length=2000)
    sense_label: str | None = Field(default=None, max_length=120)


class WordUpdateIn(BaseModel):
    deck_id: UUID | None = None
    term: str | None = Field(default=None, min_length=1, max_length=255)
    meaning: str | None = Field(default=None, min_length=1, max_length=2000)
    definition: str | None = Field(default=None, max_length=2000)
    example: str | None = Field(default=None, max_length=2000)
    sense_label: str | None = Field(default=None, max_length=120)


class WordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deck_id: UUID
    term: str
    meaning: str
    definition: str | None
    example: str | None
    sense_label: str | None
    box: LeitnerBox
    due_at: datetime
    review_count: int
    last_reviewed_at: datetime | None
    created_at: datetime
