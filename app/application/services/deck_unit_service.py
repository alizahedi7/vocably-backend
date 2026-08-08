"""Unit (lesson/chapter) use cases, authorized by deck membership."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.services.deck_access import DeckAccess
from app.core.exceptions import NotFoundError, ValidationError
from app.domain.entities.deck_unit import MAX_UNIT_NAME_LENGTH, DeckUnit
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.deck_unit_repository import DeckUnitRepository


class DeckUnitService:
    def __init__(self, units: DeckUnitRepository, members: DeckMemberRepository) -> None:
        self._units = units
        self._access = DeckAccess(members)

    async def list_units(self, deck_id: UUID, user_id: UUID) -> list[DeckUnit]:
        await self._access.require_read(deck_id, user_id)
        return await self._units.list_for_deck(deck_id)

    async def create(self, deck_id: UUID, user_id: UUID, *, name: str) -> DeckUnit:
        await self._access.require_edit_words(deck_id, user_id)
        unit = DeckUnit(
            deck_id=deck_id,
            name=_clean_name(name),
            position=await self._units.next_position(deck_id),
        )
        return await self._units.add(unit)

    async def rename(self, unit_id: UUID, user_id: UUID, *, name: str) -> DeckUnit:
        unit = await self._get_editable(unit_id, user_id)
        unit.name = _clean_name(name)
        unit.updated_at = datetime.now(UTC)
        return await self._units.update(unit)

    async def delete(self, unit_id: UUID, user_id: UUID) -> int:
        """Delete a unit and report how many cards came loose."""
        await self._get_editable(unit_id, user_id)
        return await self._units.delete(unit_id)

    async def resolve_for_deck(self, unit_id: UUID, deck_id: UUID) -> UUID:
        """Check a unit belongs to ``deck_id``, for a word write.

        A ``unit_id`` from another deck is a 422, not a silent write: accepting
        it would put a card in a heading nobody in its deck can see.
        """
        unit = await self._units.get(unit_id)
        if unit is None or unit.deck_id != deck_id:
            raise ValidationError("That unit is not part of this deck.")
        return unit.id

    async def _get_editable(self, unit_id: UUID, user_id: UUID) -> DeckUnit:
        unit = await self._units.get(unit_id)
        if unit is None:
            raise NotFoundError("Unit not found.")
        # Membership is checked against the unit's own deck, so a unit id from
        # a deck the caller cannot see 404s rather than confirming it exists.
        await self._access.require_edit_words(unit.deck_id, user_id)
        return unit


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValidationError("Give the unit a name")
    if len(cleaned) > MAX_UNIT_NAME_LENGTH:
        raise ValidationError(f"Unit names are up to {MAX_UNIT_NAME_LENGTH} characters.")
    return cleaned
