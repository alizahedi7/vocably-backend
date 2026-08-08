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
    #: Whether this deck's words wait to be started by this member.
    #:
    #: True for a deck that arrived from elsewhere — an Explore copy, an
    #: accepted share, an invite code — where a word with no progress row is
    #: *not started* rather than new-and-due. False for a deck the member built
    #: themselves, where adding a card already said "I am learning this".
    self_paced: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_owner(self) -> bool:
        return self.role is DeckRole.OWNER
