"""Word (flashcard) use cases, authorized by deck membership.

Reads return a ``StudiedWord`` — the shared card plus the caller's own progress
— so the wire shape is unchanged even though the two halves now live in
different tables. Writes touch only the card, which every editor of the deck
shares.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.services.deck_access import DeckAccess
from app.core.exceptions import NotFoundError, ValidationError
from app.domain.entities.studied_word import StudiedWord
from app.domain.entities.word import Word
from app.domain.entities.word_progress import WordProgress
from app.domain.entities.xp import XpAction
from app.domain.enums import LeitnerBox
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.deck_unit_repository import DeckUnitRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_progress_repository import WordProgressRepository
from app.domain.repositories.word_repository import WordRepository
from app.domain.repositories.xp_repository import XpRepository
from app.domain.services.calendar import day_start_for, today_for


@dataclass(frozen=True, slots=True)
class StartResult:
    """What a start (or an undo) changed, for a client that shows it at once."""

    #: The cards that were selected, so an undo knows what to take back out.
    started_ids: list[UUID]
    #: How many rows were actually written. Lower than ``len(started_ids)`` when
    #: another device got there first; negative for an undo.
    started: int
    #: Cards in the deck still waiting to be started, so the screen can say
    #: "484 left" without asking again.
    remaining: int


class _Unset:
    """Sentinel for "the caller did not mention this field".

    ``None`` is a real value for ``unit_id`` — it means "take this card out of
    its unit" — so absence needs its own marker rather than sharing one with it.
    """

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return "UNSET"


UNSET = _Unset()


class WordService:
    def __init__(
        self,
        words: WordRepository,
        progress: WordProgressRepository,
        members: DeckMemberRepository,
        units: DeckUnitRepository,
        xp: XpRepository,
        users: UserRepository,
    ) -> None:
        self._words = words
        self._progress = progress
        self._units = units
        self._xp = xp
        self._users = users
        self._access = DeckAccess(members)

    async def list_words(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
        box: LeitnerBox | None = None,
    ) -> list[StudiedWord]:
        if deck_id is not None:
            await self._access.require_read(deck_id, user_id)
        return await self._progress.list_for_user(
            user_id,
            deck_id=deck_id,
            limit=limit,
            offset=offset,
            box=box,
            day_start=await self._day_start(user_id),
        )

    async def get_readable(self, word_id: UUID, user_id: UUID) -> StudiedWord:
        """The card plus this learner's progress, if they are in its deck.

        404 rather than 403 for a non-member: a stranger walking word ids must
        not be able to learn which ones exist.
        """
        studied = await self._progress.get_for_user(
            word_id, user_id, day_start=await self._day_start(user_id)
        )
        if studied is None:
            raise NotFoundError("Word not found.")
        return studied

    async def _day_start(self, user_id: UUID) -> datetime:
        """Where this learner's day begins, so a card reports the right due date."""
        user = await self._users.get(user_id)
        return day_start_for(user.timezone if user else None)

    async def create(
        self,
        user_id: UUID,
        *,
        deck_id: UUID,
        term: str,
        meaning: str,
        example: str | None,
        sense_label: str | None,
        definition: str | None = None,
        phonetic: str | None = None,
        unit_id: UUID | None = None,
    ) -> StudiedWord:
        member = await self._access.require_edit_words(deck_id, user_id)
        if unit_id is not None:
            await self._assert_unit_in_deck(unit_id, deck_id)
        word = Word(
            created_by_user_id=user_id,
            deck_id=deck_id,
            unit_id=unit_id,
            term=term.strip(),
            meaning=meaning.strip(),
            definition=(definition or "").strip() or None,
            example=(example or "").strip() or None,
            sense_label=sense_label,
            phonetic=(phonetic or "").strip() or None,
        )
        created = await self._words.add(word)
        # Adding a card is work, whether it was typed or came from the reader.
        now = datetime.now(UTC)
        user = await self._users.get(user_id)
        await self._xp.award(
            user_id,
            XpAction.ADD_WORD,
            occurred_at=now,
            day=today_for(user.timezone if user else None, now),
            ref_type="word",
            ref_id=created.id,
        )
        # No progress row is written: the card is new to everyone, and an
        # unstudied word already reads as box 1, due now.
        #
        # Except in a self-paced deck, where a missing row means *not started*.
        # Whoever typed the card is plainly learning it — asking them to add
        # their own word to their own boxes would be the second step this
        # feature exists to remove. Everyone else in the deck still starts it
        # themselves.
        if member.self_paced:
            day_start = day_start_for(user.timezone if user else None, now)
            await self._progress.start_words(
                user_id,
                deck_id,
                [created.id],
                now,
                due_at=WordProgress.first_due_at(day_start),
            )
        return await self.get_readable(created.id, user_id)

    async def start(
        self,
        user_id: UUID,
        deck_id: UUID,
        *,
        word_ids: list[UUID] | None = None,
        unit_id: UUID | None = None,
        count: int | None = None,
    ) -> StartResult:
        """Put some of a saved deck's cards into the learner's boxes.

        Three ways to say which, because there are three ways people work
        through a 500-word deck: *these cards* (word_ids — tapped one at a
        time), *this unit* (unit_id — a lesson at a time), or *the next N*
        (count — the batch the deck screen offers). They compose: a unit plus a
        count is "the next ten of Unit 3".

        Reading the deck is enough. Starting a card writes nothing anyone else
        can see — it is this learner's own queue — so a viewer of a class deck
        may do it exactly as the owner may.
        """
        await self._access.require_read(deck_id, user_id)
        if count is not None and count <= 0:
            raise ValidationError("Choose at least one word")
        ids = await self._progress.list_unstarted(
            user_id, deck_id, unit_id=unit_id, word_ids=word_ids, limit=count
        )
        now = datetime.now(UTC)
        user = await self._users.get(user_id)
        # Starting a word is meeting it, and a word met today is reviewed
        # tomorrow — the same rule as a word typed today.
        due_at = WordProgress.first_due_at(day_start_for(user.timezone if user else None, now))
        started = await self._progress.start_words(user_id, deck_id, ids, now, due_at=due_at)
        remaining = len(await self._progress.list_unstarted(user_id, deck_id))
        return StartResult(started_ids=ids, started=started, remaining=remaining)

    async def unstart(self, user_id: UUID, deck_id: UUID, word_ids: list[UUID]) -> StartResult:
        """Undo a start. Cards that have been answered are left alone.

        This is what makes "Added 20 words · Undo" safe to offer: the toast can
        call it blind, and a card the learner has already reviewed in the
        meantime keeps its box.
        """
        await self._access.require_read(deck_id, user_id)
        removed = await self._progress.unstart_words(user_id, word_ids)
        remaining = len(await self._progress.list_unstarted(user_id, deck_id))
        return StartResult(started_ids=[], started=-removed, remaining=remaining)

    async def update(
        self,
        word_id: UUID,
        user_id: UUID,
        *,
        term: str | None = None,
        meaning: str | None = None,
        definition: str | None = None,
        example: str | None = None,
        sense_label: str | None = None,
        phonetic: str | None = None,
        deck_id: UUID | None = None,
        unit_id: UUID | None | _Unset = UNSET,
    ) -> StudiedWord:
        studied = await self.get_readable(word_id, user_id)
        await self._access.require_edit_words(studied.deck_id, user_id)
        word = studied.word
        # Captured before the move: `studied.deck_id` reads through to `word`,
        # so after reassigning it the two would compare equal and the
        # unit-clearing branch below would never fire.
        original_deck_id = word.deck_id
        if deck_id is not None and deck_id != word.deck_id:
            # Both ends: you must be allowed to take it out of where it is and
            # to put it where it is going.
            await self._access.require_edit_words(deck_id, user_id)
            word.deck_id = deck_id
        if term is not None:
            # Re-spelling the card invalidates its transcription: /rʌn/ under a
            # term that now reads "ran" is a wrong IPA, and a wrong IPA teaches
            # a wrong sound. Drop it and let the backfill put the right one
            # back, unless this same request supplies one.
            if term.strip() != word.term and phonetic is None:
                word.phonetic = None
            word.term = term.strip()
        if meaning is not None:
            word.meaning = meaning.strip()
        # Clearing is expressed as "" — an omitted field leaves the definition
        # alone, which is what keeps a client that predates it from wiping one.
        if definition is not None:
            word.definition = definition.strip() or None
        if example is not None:
            word.example = example.strip() or None
        if sense_label is not None:
            word.sense_label = sense_label
        if phonetic is not None:
            word.phonetic = phonetic.strip() or None
        if not isinstance(unit_id, _Unset):
            # Explicit null clears; omission left it alone above.
            if unit_id is not None:
                await self._assert_unit_in_deck(unit_id, word.deck_id)
            word.unit_id = unit_id
        elif deck_id is not None and deck_id != original_deck_id:
            # A card moved to another deck cannot keep a unit belonging to the
            # deck it left, and the client does not know to clear it.
            word.unit_id = None
        word.updated_at = datetime.now(UTC)
        await self._words.update(word)
        return await self.get_readable(word_id, user_id)

    async def _assert_unit_in_deck(self, unit_id: UUID, deck_id: UUID) -> None:
        unit = await self._units.get(unit_id)
        if unit is None or unit.deck_id != deck_id:
            raise ValidationError("That unit is not part of this deck.")

    async def delete(self, word_id: UUID, user_id: UUID) -> None:
        studied = await self.get_readable(word_id, user_id)
        await self._access.require_edit_words(studied.deck_id, user_id)
        # Everyone's progress on the card goes with it, by cascade: the card is
        # gone, so nobody's boxes against it mean anything any more.
        await self._words.delete(word_id)
