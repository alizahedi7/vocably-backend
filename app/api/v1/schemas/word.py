"""Word request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import LeitnerBox


class WordCreateIn(BaseModel):
    deck_id: UUID
    term: str = Field(min_length=1, max_length=255)
    meaning: str = Field(min_length=1)
    example: str | None = None
    sense_label: str | None = Field(default=None, max_length=120)


class WordUpdateIn(BaseModel):
    deck_id: UUID | None = None
    term: str | None = Field(default=None, min_length=1, max_length=255)
    meaning: str | None = Field(default=None, min_length=1)
    example: str | None = None
    sense_label: str | None = Field(default=None, max_length=120)


class WordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deck_id: UUID
    term: str
    meaning: str
    example: str | None
    sense_label: str | None
    box: LeitnerBox
    due_at: datetime
    review_count: int
    last_reviewed_at: datetime | None
    created_at: datetime
