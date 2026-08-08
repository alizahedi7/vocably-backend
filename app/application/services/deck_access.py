"""The one place that answers "what may this user do with this deck?".

Every deck- and word-scoped use case asks the same question, and it used to be
asked four different times as ``entity.user_id == user_id``. Under shared decks
that question has an answer with three values, and getting it wrong in one place
is a data leak — so it is asked here, once.

Note what a non-member gets: :class:`NotFoundError`, not
:class:`PermissionDeniedError`. Answering 403 to someone who is not in the deck
confirms that a deck with that id exists, which is exactly what a stranger
probing ids is trying to learn. A member who lacks the *role* for an action does
get a 403 — at that point they already know the deck exists.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.domain.entities.deck_member import DeckMember
from app.domain.enums import DeckRole
from app.domain.repositories.deck_member_repository import DeckMemberRepository


class DeckAccess:
    def __init__(self, members: DeckMemberRepository) -> None:
        self._members = members

    async def membership(self, deck_id: UUID, user_id: UUID) -> DeckMember:
        """This user's membership, or 404 if they have none."""
        member = await self._members.get(deck_id, user_id)
        if member is None:
            raise NotFoundError("Deck not found.")
        return member

    async def require_read(self, deck_id: UUID, user_id: UUID) -> DeckMember:
        """Any member may read the deck, its words and its units, and study them."""
        return await self.membership(deck_id, user_id)

    async def require_edit_words(self, deck_id: UUID, user_id: UUID) -> DeckMember:
        """Owners and editors may add, change and delete words and units."""
        member = await self.membership(deck_id, user_id)
        if not member.role.can_edit_words:
            raise PermissionDeniedError("You can study this deck, but not change it.")
        return member

    async def require_manage(self, deck_id: UUID, user_id: UUID) -> DeckMember:
        """Only the owner may invite, remove, change roles, or delete the deck."""
        member = await self.membership(deck_id, user_id)
        if not member.role.can_manage_members:
            raise PermissionDeniedError("Only the deck's owner can do that.")
        return member

    @staticmethod
    def owner(deck_id: UUID, user_id: UUID, *, self_paced: bool = False) -> DeckMember:
        """The membership row a newly created deck's creator gets.

        ``self_paced`` is the one thing that differs between a deck someone
        typed and one they *copied* from Explore: both are theirs outright, but
        nobody chose the five hundred words in the second one card by card.
        """
        return DeckMember(
            deck_id=deck_id, user_id=user_id, role=DeckRole.OWNER, self_paced=self_paced
        )
