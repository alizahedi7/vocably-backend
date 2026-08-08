"""Word (flashcard) use cases, authorized by deck membership.

Reads return a ``StudiedWord`` — the shared card plus the caller's own progress
— so the wire shape is unchanged even though the two halves now live in
different tables. Writes touch only the card, which every editor of the deck
shares.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.services.deck_access import DeckAccess
from app.core.exceptions import NotFoundError, ValidationError
from app.domain.entities.studied_word import StudiedWord
from app.domain.entities.word import Word
from app.domain.entities.xp import XpAction
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.deck_unit_repository import DeckUnitRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_progress_repository import WordProgressRepository
from app.domain.repositories.word_repository import WordRepository
from app.domain.repositories.xp_repository import XpRepository
from app.domain.services.calendar import today_for


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
    ) -> list[StudiedWord]:
        if deck_id is not None:
            await self._access.require_read(deck_id, user_id)
        return await self._progress.list_for_user(
            user_id, deck_id=deck_id, limit=limit, offset=offset
        )

    async def get_readable(self, word_id: UUID, user_id: UUID) -> StudiedWord:
        """The card plus this learner's progress, if they are in its deck.

        404 rather than 403 for a non-member: a stranger walking word ids must
        not be able to learn which ones exist.
        """
        studied = await self._progress.get_for_user(word_id, user_id)
        if studied is None:
            raise NotFoundError("Word not found.")
        return studied

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
        await self._access.require_edit_words(deck_id, user_id)
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
        return await self.get_readable(created.id, user_id)

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
