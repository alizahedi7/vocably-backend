"""Membership of a deck — the authorization fact every deck route asks about."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.enums import DeckRole


@dataclass(slots=True)
class DeckMember:
    deck_id: UUID = field(default_factory=uuid4)
    user_id: UUID = field(default_factory=uuid4)
    role: DeckRole = DeckRole.VIEWER
    invited_by_user_id: UUID | None = None
    joined_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_owner(self) -> bool:
        return self.role is DeckRole.OWNER
