"""Port: finding decks the learner did not make."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.deck import Deck
from app.domain.entities.word import Word


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
    #: Whether the person browsing already took a copy of this deck. Answered
    #: from ``decks.copied_from_deck_id`` rather than remembered on the device,
    #: so the tick survives a reinstall and agrees across a phone and the PWA.
    #: ``False`` whenever nobody in particular is browsing.
    saved: bool = False


@dataclass(frozen=True, slots=True)
class DeckPublication:
    """Whether a deck is in Explore, for a caller that already knows the deck.

    Deliberately not a :class:`PublicDeckView`: that shape is the *listing*, and
    it is readable only for a deck that is already public — a private deck and a
    deck that does not exist both come back as ``None``. The admin surface has to
    tell those two apart, because the whole point is to offer "publish" for one
    and "remove from Explore" for the other.
    """

    is_public: bool
    is_official: bool
    #: When it went into Explore. ``None`` whenever ``is_public`` is false:
    #: unpublishing clears it rather than leaving behind a date that reads live.
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class PublicUnitView:
    """One section of a published deck, as the preview lists it.

    Carries its own ``word_count`` so the preview can show the shape of a
    coursebook — twelve lessons of forty — without fetching two thousand cards
    to count them.
    """

    id: UUID
    name: str
    position: int
    word_count: int


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
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        limit: int,
        offset: int,
        viewer_id: UUID | None = None,
    ) -> list[PublicDeckView]:
        """Published decks, newest first. One query, counts included.

        ``viewer_id`` only fills in :attr:`PublicDeckView.saved`; omitting it
        answers ``False`` for everyone rather than failing, which is what the
        deck-build tooling wants.
        """

    @abstractmethod
    async def get_public(
        self, deck_id: UUID, *, viewer_id: UUID | None = None
    ) -> PublicDeckView | None: ...

    @abstractmethod
    async def list_public_units(self, deck_id: UUID) -> list[PublicUnitView]:
        """The deck's sections, in the author's order, with their card counts.

        Visibility is the caller's business — this reads a deck by id and says
        nothing about whether it is published.
        """

    @abstractmethod
    async def list_public_words(
        self,
        deck_id: UUID,
        *,
        unit_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> list[Word]:
        """A page of the deck's cards, in the deck's own order.

        The *cards*, with no progress: the person reading has none, and a box
        or a due date invented for them would be a lie about a deck they have
        not started. Ordered oldest-first like a copy, so a coursebook previews
        in the order it is meant to be worked through.
        """

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
    async def set_listing_metadata(
        self,
        deck_id: UUID,
        *,
        category: str,
        description: str,
        description_fa: str,
    ) -> None:
        """Update what Explore *shows*, without touching whether it shows it.

        Deliberately separate from :meth:`set_published`, which asserts
        ``is_public`` and ``published_at`` and would therefore unpublish a live
        deck if it were reused to fix a description. Publishing stays one
        deliberate act; re-wording is another.
        """

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
    async def publication_of(self, deck_id: UUID) -> DeckPublication | None:
        """Where a deck stands with Explore, or ``None`` if there is no such deck.

        The read half of :meth:`set_published`, and the only one that answers for
        a *private* deck — which is exactly the case the admin screen asks about
        before deciding whether publishing is a meaningful thing to offer.
        """

    @abstractmethod
    async def list_shares_for(self, user_id: UUID) -> list[SharedDeckView]:
        """Offers waiting for this user to answer, newest first.

        **Unanswered only, and that is the whole contract** — the recipient's
        mirror of :meth:`list_pending_shares_of`. This used to return every
        share row ever addressed to them, accepted ones included, which is what
        made the Shared tab a permanent record instead of an inbox: a deck
        taken months ago still sat there as a card saying it had been taken,
        and there was no action left to perform on it. Worse, the row outlived
        the membership it created — an owner who removed somebody left them
        looking at a deck they could no longer open.

        An answered offer is not "an offer with a flag on it"; it has become
        something else, and something else already reports it. Accepting makes
        a membership, which the deck list shows. Declining deletes the row.
        Neither belongs here.
        """

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
    async def withdraw(self, share_id: UUID) -> None:
        """Delete the offer. Both answers end here.

        Accepting and declining differ in what they leave behind — a membership
        or nothing — but they agree that the *offer* is over, so neither keeps
        the row. There was a ``mark_accepted`` beside this once, which flagged
        the row instead and is what kept answered offers in the recipient's
        inbox forever. Deleting also means a re-share after somebody has been
        removed from the deck lands as a fresh, visible offer, where the
        flagged row would have been silently upserted back into "accepted" and
        never shown to them again.
        """
