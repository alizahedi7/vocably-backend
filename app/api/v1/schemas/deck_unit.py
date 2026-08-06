"""Deck unit (lesson/chapter) request/response schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities.deck_unit import MAX_UNIT_NAME_LENGTH


class DeckUnitCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_UNIT_NAME_LENGTH)


class DeckUnitUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_UNIT_NAME_LENGTH)


class DeckUnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deck_id: UUID
    name: str
    position: int
