"""Finding decks the learner did not make.

The distinction that runs through this module: **saving from Explore takes a
copy, sharing with a person shares the deck.** A published deck is a starting
point, so editing your copy must not change anyone else's; a deck sent to a
friend or a class is the same deck, with separate progress.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.services.deck_access import DeckAccess
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.entities.deck import Deck
from app.domain.entities.deck_member import DeckMember
from app.domain.entities.word import Word
from app.domain.enums import DeckRole
from app.domain.repositories.deck_discovery_repository import (
    DeckDiscoveryRepository,
    OutgoingShareView,
    PublicDeckView,
    PublicUnitView,
    SharedDeckView,
)
from app.domain.repositories.deck_invite_repository import DeckInviteRepository
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.friend_repository import FriendRepository
from app.domain.repositories.user_repository import UserRepository

#: Explore returns a page, not a catalogue. The client sends no paging yet, so
#: this is what it gets — large enough to fill the tab, bounded so a growing
#: catalogue never turns the screen into a full-table read.
DEFAULT_PUBLIC_LIMIT = 50
MAX_PUBLIC_LIMIT = 100

#: A preview is read, not studied, so its words page like any list. The ceiling
#: is generous because the client pages until the server runs out — a cap that
#: silently truncates is the bug ``GET /words`` already had once, and a preview
#: showing eight of a lesson's twelve cards would misrepresent the deck.
DEFAULT_PREVIEW_WORD_LIMIT = 100
MAX_PREVIEW_WORD_LIMIT = 500


class DeckDiscoveryService:
    def __init__(
        self,
        discovery: DeckDiscoveryRepository,
        members: DeckMemberRepository,
        invites: DeckInviteRepository,
        users: UserRepository,
        friends: FriendRepository,
    ) -> None:
        self._discovery = discovery
        self._members = members
        self._invites = invites
        self._users = users
        self._friends = friends
        self._access = DeckAccess(members)

    # ── Explore ──────────────────────────────────────────────
    async def list_public(
        self,
        *,
        viewer_id: UUID | None = None,
        category: str | None = None,
        query: str | None = None,
        limit: int = DEFAULT_PUBLIC_LIMIT,
        offset: int = 0,
    ) -> list[PublicDeckView]:
        return await self._discovery.list_public(
            category=category,
            query=query,
            limit=min(max(limit, 1), MAX_PUBLIC_LIMIT),
            offset=max(offset, 0),
            viewer_id=viewer_id,
        )

    async def get_public(self, deck_id: UUID, viewer_id: UUID) -> PublicDeckView:
        """One published deck, for the preview a learner reads before saving.

        Same visibility rule as :meth:`import_public`: checked against
        ``is_public``, never membership, or the endpoint becomes a way to read a
        private deck by id.
        """
        public = await self._discovery.get_public(deck_id, viewer_id=viewer_id)
        if public is None:
            raise NotFoundError("Deck not found")
        return public

    async def list_public_units(self, deck_id: UUID, viewer_id: UUID) -> list[PublicUnitView]:
        await self.get_public(deck_id, viewer_id)
        return await self._discovery.list_public_units(deck_id)

    async def list_public_words(
        self,
        deck_id: UUID,
        viewer_id: UUID,
        *,
        unit_id: UUID | None = None,
        limit: int = DEFAULT_PREVIEW_WORD_LIMIT,
        offset: int = 0,
    ) -> list[Word]:
        """A page of a published deck's cards, so it can be read before saving.

        The visibility check comes first and is the whole security story: this
        returns card content to someone who is not a member, which is only ever
        acceptable because the deck's owner published it.
        """
        await self.get_public(deck_id, viewer_id)
        return await self._discovery.list_public_words(
            deck_id,
            unit_id=unit_id,
            limit=min(max(limit, 1), MAX_PREVIEW_WORD_LIMIT),
            offset=max(offset, 0),
        )

    async def import_public(self, deck_id: UUID, user_id: UUID) -> Deck:
        """Take a copy of a published deck.

        Visibility is checked against ``is_public`` rather than membership: a
        private deck must 404 here exactly as it would anywhere else, or the
        endpoint becomes a way to read one by id.
        """
        public = await self._discovery.get_public(deck_id)
        if public is None:
            raise NotFoundError("Deck not found")

        copy = await self._discovery.copy_deck_to(deck_id, user_id)
        # The copier owns their copy outright — membership, like every deck.
        # Self-paced, though: nobody picked these five hundred words card by
        # card, so they wait in the deck until the learner starts them rather
        # than landing in one day's review queue.
        await self._members.add_if_absent(DeckAccess.owner(copy.id, user_id, self_paced=True))
        await self._discovery.increment_saves(deck_id)
        return copy

    async def set_published(
        self,
        deck_id: UUID,
        *,
        is_public: bool,
        is_official: bool = False,
        category: str | None = None,
        description: str | None = None,
        description_fa: str | None = None,
    ) -> None:
        """Put a deck in Explore, or take it out. **Admins only.**

        Ordinary owners cannot publish: there is no report path and no
        moderation queue, and an open publish button without one is an
        unreviewed-content problem rather than a feature. The gate is the
        route's ``CurrentAdmin`` dependency; this method assumes it passed.
        """
        await self._assert_deck_exists(deck_id)
        await self._discovery.set_published(
            deck_id,
            is_public=is_public,
            is_official=is_official,
            category=category,
            description=description,
            description_fa=description_fa,
            published_at=datetime.now(UTC) if is_public else None,
        )

    async def _assert_deck_exists(self, deck_id: UUID) -> None:
        # Membership is not the check here: an admin curating Explore is not a
        # member of the decks they publish.
        if await self._members.list_for_deck(deck_id) == []:
            raise NotFoundError("Deck not found")

    # ── person-to-person ─────────────────────────────────────
    async def list_shared_with(self, user_id: UUID) -> list[SharedDeckView]:
        """The learner's inbox: offers they have not answered yet.

        Unanswered only — see the repository method for why an accepted offer
        is not an offer with a flag on it.
        """
        return await self._discovery.list_shares_for(user_id)

    async def list_pending_shares(self, deck_id: UUID, user_id: UUID) -> list[OutgoingShareView]:
        """Who has been offered this deck and not answered.

        Whoever may invite may see it: the list is of decisions they made, and
        it is what stops a second invite being sent to someone who is already
        holding the first.
        """
        await self._access.require_manage(deck_id, user_id)
        return await self._discovery.list_pending_shares_of(deck_id)

    async def share(
        self,
        deck_id: UUID,
        user_id: UUID,
        *,
        to_username: str,
        role: DeckRole = DeckRole.VIEWER,
    ) -> str:
        """Offer one of your decks to a handle.

        Only someone who may already invite to the deck can share it, so a
        viewer cannot hand a teacher's deck around.

        ``role`` is what *accepting* will make them, and it is carried on the
        offer rather than applied now: the recipient is not a member until they
        say so. There is exactly one owner, so asking for that one is answered
        with a viewer instead of an error — the same downgrade every other role
        entry point in this service makes.

        **Sharing does not open the deck's invite link, and must not.** This
        used to end by setting ``is_open = True`` and returning the code, so
        that the sharer had something to paste anywhere. Two things were wrong
        with it. An invite code is a *bearer credential over the whole deck* —
        holding it is what grants access, to anyone, at ``invite.role`` — and
        minting one as a silent side effect of naming one person meant a
        teacher who invited a single student by handle had a live public join
        link on their deck without ever asking for one, and no indication that
        they did. And it is the owner's decision to make: the link is its own
        control in the share sheet, with its own switch, and this quietly
        overrode it — turning back on, at the next share, a link the owner had
        deliberately closed.

        It returns the code of a link that is **already** open, and an empty
        string otherwise: there is nothing to paste when there is no link, and
        answering with a code that ``join`` would refuse (it requires
        ``accepts()``) would be worse than answering with nothing.
        """
        await self._access.require_manage(deck_id, user_id)

        handle = to_username.strip().lower()
        if not handle:
            raise ValidationError("Enter a handle to share with")
        recipient = await self._users.get_by_username(handle)
        if recipient is None:
            raise ValidationError("No one uses that handle")
        if recipient.id == user_id:
            raise ValidationError("That is your own handle")
        if await self._members.get(deck_id, recipient.id) is not None:
            raise ConflictError("They already have this deck")

        now = datetime.now(UTC)
        await self._discovery.offer(
            deck_id,
            from_user_id=user_id,
            to_user_id=recipient.id,
            role=(DeckRole.VIEWER if role is DeckRole.OWNER else role).value,
            shared_at=now,
        )
        # Sharing is what puts someone in the friends list — the client calls
        # this `noteShared`, and it is why a handle is only ever typed once.
        await self._friends.link(user_id, recipient.id, shared_at=now)

        invite = await self._invites.get_for_deck(deck_id)
        return invite.code if invite and invite.accepts(now) else ""

    async def accept(self, share_id: UUID, user_id: UUID) -> Deck:
        """Take a deck someone offered. The same deck, not a copy.

        The offer is **consumed**, not flagged. Once the membership exists the
        share row has nothing left to say: the deck is in the learner's own
        list, and a card in the inbox reading "you accepted this" is a question
        with no answer left to give. Keeping it was also how somebody removed
        from a deck went on seeing it under Shared long after they had lost
        access to it — the offer outliving the membership it made.
        """
        share = await self._require_own_share(share_id, user_id)
        await self._members.add_if_absent(
            DeckMember(
                deck_id=share.deck.id,
                user_id=user_id,
                # Whatever the sender offered; DeckRole.parse fails closed to
                # viewer if the stored value is one this deploy does not know.
                role=DeckRole.parse(share.role),
                # Someone else's list: the learner decides what enters their
                # boxes, and when.
                self_paced=True,
            )
        )
        await self._discovery.withdraw(share_id)
        return share.deck

    async def decline(self, share_id: UUID, user_id: UUID) -> None:
        """Stop being offered it. The sender is not told."""
        await self._require_own_share(share_id, user_id)
        await self._discovery.withdraw(share_id)

    async def _require_own_share(self, share_id: UUID, user_id: UUID) -> SharedDeckView:
        share = await self._discovery.get_share(share_id)
        if share is None:
            raise NotFoundError("Deck not found")
        if share.to_user_id != user_id:
            # 404, not 403: a share id that is not yours must not be
            # distinguishable from one that does not exist.
            raise NotFoundError("Deck not found")
        return share
