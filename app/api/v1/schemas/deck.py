"""Deck request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.dto import DeckView
from app.domain.enums import DeckRole


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
    #: Every card in the deck. The same number for every member.
    word_count: int
    #: Of those, the ones in this learner's boxes. Equal to ``word_count``
    #: except in a self-paced deck they are still working through. Clients
    #: older than the field ignore it and read the deck exactly as before.
    started_count: int
    due_count: int
    progress_pct: int
    #: What the caller may do with this deck, without a request per deck. The
    #: client shows "delete" or "leave" on the strength of it; the server still
    #: decides, on every write.
    role: DeckRole
    is_owner: bool
    #: True when this deck's words wait to be started — an Explore copy, a
    #: share, an invite. The client shows the "add to my boxes" controls on it.
    self_paced: bool

    @classmethod
    def from_view(cls, view: DeckView) -> DeckWithStatsOut:
        d = view.deck
        return cls(
            id=d.id,
            name=d.name,
            hue=d.hue,
            initial=d.initial,
            word_count=view.word_count,
            started_count=view.started_count,
            due_count=view.due_count,
            progress_pct=view.progress_pct,
            role=view.role,
            is_owner=view.role is DeckRole.OWNER,
            self_paced=view.self_paced,
        )
