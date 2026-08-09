"""Explore, person-to-person shares, and friends.

Field names are the contract with ``lib/models/shared_deck.dart`` and
``lib/models/friend.dart``; both parsers default every key, but the *names*
must match or the screens render empty.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DeckRole
from app.domain.repositories.deck_discovery_repository import (
    OutgoingShareView,
    PublicDeckView,
    PublicUnitView,
    SharedDeckView,
)
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
    #: Whether this learner already took a copy. The Explore card reads it to
    #: show a tick instead of "Save" — a second copy of the same deck is never
    #: what someone tapping twice meant. Additive and defaulted, so a client
    #: that predates it parses the response unchanged.
    saved: bool = False

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
            saved=view.saved,
        )


class PublicDecksOut(BaseModel):
    """Wrapped, not a bare array — the client reads ``decks``."""

    decks: list[PublicDeckOut]


class PublicUnitOut(BaseModel):
    """One section of a published deck, in the preview's section list."""

    id: UUID
    name: str
    position: int
    word_count: int

    @classmethod
    def from_view(cls, view: PublicUnitView) -> PublicUnitOut:
        return cls(id=view.id, name=view.name, position=view.position, word_count=view.word_count)


class PublicDeckDetailOut(PublicDeckOut):
    """A published deck with its sections — what the preview screen opens on.

    The sections but not the cards: a coursebook's shape is twelve lessons of
    forty, and fetching two thousand cards to draw that is a page the learner
    has not asked to read yet. The words come from
    ``GET /decks/public/{id}/words``, a section at a time.
    """

    units: list[PublicUnitOut]

    @classmethod
    def from_views(cls, view: PublicDeckView, units: list[PublicUnitView]) -> PublicDeckDetailOut:
        return cls(
            **PublicDeckOut.from_view(view).model_dump(),
            units=[PublicUnitOut.from_view(u) for u in units],
        )


class PublicWordOut(BaseModel):
    """A card as someone who has not saved the deck may read it.

    Deliberately not :class:`~app.api.v1.schemas.word.WordOut`: ``box``,
    ``started`` and ``due_at`` are one learner's progress against a card, and
    this reader has none of it. Placeholder values would be a claim about a deck
    they have not begun.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    unit_id: UUID | None
    term: str
    meaning: str
    definition: str | None
    example: str | None
    phonetic: str | None


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
    #: What accepting will make them. Optional and defaulting to viewer, so a
    #: client that predates the field keeps the behaviour it already had — and
    #: because handing someone edit rights is the bigger of the two decisions,
    #: it should never be what happens by omission.
    role: DeckRole = DeckRole.VIEWER


class ShareDeckOut(BaseModel):
    """The deck's invite code, so the sharer has something to paste anywhere."""

    code: str


class PendingShareOut(BaseModel):
    """An offer of this deck that has not been answered yet."""

    username: str
    name: str
    role: DeckRole
    shared_at: datetime

    @classmethod
    def from_view(cls, view: OutgoingShareView) -> PendingShareOut:
        return cls(
            username=view.to_username,
            name=view.to_name,
            role=DeckRole.parse(view.role),
            shared_at=view.shared_at,
        )


class PendingSharesOut(BaseModel):
    shares: list[PendingShareOut]


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
