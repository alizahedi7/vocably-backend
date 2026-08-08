"""Explore, person-to-person shares, and friends.

Field names are the contract with ``lib/models/shared_deck.dart`` and
``lib/models/friend.dart``; both parsers default every key, but the *names*
must match or the screens render empty.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.repositories.deck_discovery_repository import PublicDeckView, SharedDeckView
from app.domain.repositories.friend_repository import FriendView


class PublicDeckOut(BaseModel):
    id: UUID
    name: str
    hue: int
    word_count: int
    author_name: str
    author_username: str
    is_official: bool
    category: str
    description: str
    description_fa: str
    saves: int

    @classmethod
    def from_view(cls, view: PublicDeckView) -> PublicDeckOut:
        return cls(
            id=view.deck.id,
            name=view.deck.name,
            hue=view.deck.hue,
            word_count=view.word_count,
            author_name=view.author_name,
            author_username=view.author_username,
            is_official=view.is_official,
            category=view.category,
            description=view.description,
            description_fa=view.description_fa,
            saves=view.saves,
        )


class PublicDecksOut(BaseModel):
    """Wrapped, not a bare array — the client reads ``decks``."""

    decks: list[PublicDeckOut]


class SharedDeckOut(BaseModel):
    #: The *share* id, not the deck's: accept and decline address the offer.
    id: UUID
    name: str
    hue: int
    word_count: int
    from_name: str
    from_username: str
    shared_at: datetime
    accepted: bool

    @classmethod
    def from_view(cls, view: SharedDeckView) -> SharedDeckOut:
        return cls(
            id=view.share_id,
            name=view.deck.name,
            hue=view.deck.hue,
            word_count=view.word_count,
            from_name=view.from_name,
            from_username=view.from_username,
            shared_at=view.shared_at,
            accepted=view.accepted,
        )


class SharedDecksOut(BaseModel):
    decks: list[SharedDeckOut]


class ShareDeckIn(BaseModel):
    to_username: str = Field(max_length=20)


class ShareDeckOut(BaseModel):
    """The deck's invite code, so the sharer has something to paste anywhere."""

    code: str


class FriendOut(BaseModel):
    username: str
    name: str
    last_shared_at: datetime | None

    @classmethod
    def from_view(cls, view: FriendView) -> FriendOut:
        return cls(username=view.username, name=view.name, last_shared_at=view.last_shared_at)


class FriendsOut(BaseModel):
    friends: list[FriendOut]


class AddFriendIn(BaseModel):
    username: str = Field(max_length=20)
