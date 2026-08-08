"""Deck use cases, authorized by deck membership."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from app.application.dto import DeckView
from app.application.services.deck_access import DeckAccess
from app.core.exceptions import NotFoundError
from app.domain.entities.deck import Deck
from app.domain.enums import LeitnerBox
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.deck_repository import DeckRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_progress_repository import WordProgressRepository
from app.domain.services.calendar import day_start_for

_MAX_BOX = int(LeitnerBox.MASTERED)


class DeckService:
    def __init__(
        self,
        decks: DeckRepository,
        progress: WordProgressRepository,
        members: DeckMemberRepository,
        users: UserRepository,
    ) -> None:
        self._decks = decks
        self._progress = progress
        self._members = members
        self._users = users
        self._access = DeckAccess(members)

    async def list_decks(self, user_id: UUID) -> list[Deck]:
        return await self._decks.list_for_user(user_id)

    async def list_with_stats(self, user_id: UUID) -> list[DeckView]:
        """Decks enriched with word/due counts and a progress percentage.

        Two queries regardless of deck count: the deck rows, and one grouped
        tally that also serves the home screen. Nothing is counted in Python
        that the database could count.
        """
        decks = await self._decks.list_for_user_with_role(user_id)
        now = datetime.now(UTC)
        user = await self._users.get(user_id)
        # The deck cards' due numbers are the home screen's, so they answer to
        # the same day boundary the overview does.
        tallies = await self._progress.tally_by_deck_and_box(
            user_id,
            now,
            day_start=day_start_for(user.timezone if user else None, now),
        )

        # words, started, box sum over started, due. Only the first counts a
        # card nobody has started: it is in the deck, and in nothing else.
        counts: dict[UUID, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        for tally in tallies:
            bucket = counts[tally.deck_id]
            bucket[0] += tally.word_count
            if not tally.started:
                continue
            bucket[1] += tally.word_count
            bucket[2] += int(tally.box) * tally.word_count
            bucket[3] += tally.due_count

        self_paced = await self._members.self_paced_deck_ids(user_id)
        views: list[DeckView] = []
        for deck, role in decks:
            count, started, box_sum, due = counts.get(deck.id, [0, 0, 0, 0])
            progress = round((box_sum / (started * _MAX_BOX)) * 100) if started else 0
            views.append(
                DeckView(
                    deck=deck,
                    word_count=count,
                    started_count=started,
                    due_count=due,
                    progress_pct=progress,
                    role=role,
                    self_paced=deck.id in self_paced,
                )
            )
        return views

    async def get_readable(self, deck_id: UUID, user_id: UUID) -> Deck:
        """The deck, if this user is a member of it. 404 otherwise."""
        await self._access.require_read(deck_id, user_id)
        deck = await self._decks.get(deck_id)
        if deck is None:  # pragma: no cover — membership CASCADEs with the deck
            raise NotFoundError("Deck not found.")
        return deck

    async def create(self, user_id: UUID, *, name: str, hue: int) -> Deck:
        deck = Deck(user_id=user_id, name=name.strip(), hue=hue)
        created = await self._decks.add(deck)
        # The creator is a member like anyone else, and membership — not
        # decks.user_id — is what every access check reads from here on.
        await self._members.add(DeckAccess.owner(created.id, user_id))
        return created

    async def update(
        self,
        deck_id: UUID,
        user_id: UUID,
        *,
        name: str | None = None,
        hue: int | None = None,
    ) -> Deck:
        # Renaming or recolouring a shared deck changes it for every member, so
        # it is an edit, not a read.
        await self._access.require_edit_words(deck_id, user_id)
        deck = await self._decks.get(deck_id)
        if deck is None:  # pragma: no cover
            raise NotFoundError("Deck not found.")
        if name is not None:
            deck.name = name.strip()
        if hue is not None:
            deck.hue = hue
        deck.updated_at = datetime.now(UTC)
        return await self._decks.update(deck)

    async def delete(self, deck_id: UUID, user_id: UUID) -> None:
        await self._access.require_manage(deck_id, user_id)
        await self._decks.delete(deck_id)
