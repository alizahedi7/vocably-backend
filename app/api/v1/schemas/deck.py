"""Deck request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.dto import DeckView


class DeckCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    hue: int = Field(default=262, ge=0, le=360)


class DeckUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    hue: int | None = Field(default=None, ge=0, le=360)


class DeckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    hue: int
    initial: str
    created_at: datetime


class DeckWithStatsOut(BaseModel):
    id: UUID
    name: str
    hue: int
    initial: str
    word_count: int
    due_count: int
    progress_pct: int

    @classmethod
    def from_view(cls, view: DeckView) -> DeckWithStatsOut:
        d = view.deck
        return cls(
            id=d.id,
            name=d.name,
            hue=d.hue,
            initial=d.initial,
            word_count=view.word_count,
            due_count=view.due_count,
            progress_pct=view.progress_pct,
        )
