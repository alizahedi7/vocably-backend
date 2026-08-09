"""Port: finding decks the learner did not make."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.deck import Deck


@dataclass(frozen=True, slots=True)
class PublicDeckView:
    """A published deck as Explore lists it.

    Deliberately not a ``Deck``: a public deck has no due count or progress for
    the person browsing it — they have not studied it — and it has an author
    and a category a personal deck does not.
    """

    deck: Deck
    word_count: int
    author_name: str
    author_username: str
    is_official: bool
    category: str
    description: str
    description_fa: str
    saves: int


@dataclass(frozen=True, slots=True)
class SharedDeckView:
    """A deck one learner sent to another, from the recipient's side."""

    share_id: UUID
    deck: Deck
    #: Who it was offered to. Checked before accepting or declining, so a
    #: share id belonging to someone else cannot be redeemed.
    to_user_id: UUID
    #: What accepting makes them.
    role: str
    word_count: int
    from_name: str
    from_username: str
    shared_at: datetime
    accepted: bool


@dataclass(frozen=True, slots=True)
class OutgoingShareView:
    """An offer of one deck, from the *sender's* side.

    The mirror image of :class:`SharedDeckView`, and deliberately a separate
    shape: the sender already knows the deck, so what is worth returning is the
    person and where the offer stands. It exists so a share sheet can say
    "pending" beside someone instead of leaving the sender wondering whether the
    share landed at all.
    """

    to_username: str
    to_name: str
    #: What accepting would make them.
    role: str
    shared_at: datetime


class DeckDiscoveryRepository(ABC):
    @abstractmethod
    async def list_public(
        self, *, category: str | None = None, query: str | None = None, limit: int, offset: int
    ) -> list[PublicDeckView]:
        """Published decks, newest first. One query, counts included."""

    @abstractmethod
    async def get_public(self, deck_id: UUID) -> PublicDeckView | None: ...

    @abstractmethod
    async def copy_deck_to(self, deck_id: UUID, user_id: UUID) -> Deck:
        """Duplicate a deck, its units and its words for ``user_id``.

        A copy, not a subscription: editing it must not change anyone else's,
        which is the whole distinction between Explore and person-to-person
        sharing. Progress is deliberately not copied — the words are new to
        this learner.
        """

    @abstractmethod
    async def increment_saves(self, deck_id: UUID) -> None: ...

    @abstractmethod
    async def set_published(
        self,
        deck_id: UUID,
        *,
        is_public: bool,
        is_official: bool,
        category: str | None,
        description: str | None,
        description_fa: str | None,
        published_at: datetime | None,
    ) -> None:
        """Publish or unpublish a deck. Admin-only; see the service."""

    @abstractmethod
    async def list_shares_for(self, user_id: UUID) -> list[SharedDeckView]: ...

    @abstractmethod
    async def get_share(self, share_id: UUID) -> SharedDeckView | None: ...

    @abstractmethod
    async def list_pending_shares_of(self, deck_id: UUID) -> list[OutgoingShareView]:
        """Offers of this deck nobody has answered yet, oldest first.

        Pending only. An accepted offer is a membership — the roster is where
        that is reported, and saying it twice in two vocabularies is how a
        screen becomes confusing. A declined one is deleted outright, because
        the sender is not told and an offer that can be re-made is better than
        a permanent record of a refusal.
        """

    @abstractmethod
    async def offer(
        self,
        deck_id: UUID,
        *,
        from_user_id: UUID,
        to_user_id: UUID,
        role: str,
        shared_at: datetime,
    ) -> None:
        """Offer a deck to one person, replacing any pending offer of the same."""

    @abstractmethod
    async def mark_accepted(self, share_id: UUID) -> None: ...

    @abstractmethod
    async def withdraw(self, share_id: UUID) -> None: ...
