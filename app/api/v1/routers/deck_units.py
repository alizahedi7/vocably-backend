"""Unit (lesson/chapter) endpoints.

Two prefixes on purpose, matching the client: units are *listed and created*
under their deck, but renamed and deleted by their own id — the client holds a
unit id and does not always know which deck it came from.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DeckUnitServiceDep
from app.api.v1.schemas.deck_unit import DeckUnitCreateIn, DeckUnitOut, DeckUnitUpdateIn

deck_units_router = APIRouter(prefix="/decks", tags=["units"])
units_router = APIRouter(prefix="/units", tags=["units"])


@deck_units_router.get("/{deck_id}/units", response_model=list[DeckUnitOut])
async def list_units(
    deck_id: UUID,
    current_user: CurrentUser,
    units: DeckUnitServiceDep,
) -> list[DeckUnitOut]:
    """A bare array, ordered by position — the shape the client parses."""
    items = await units.list_units(deck_id, current_user.id)
    return [DeckUnitOut.model_validate(u) for u in items]


@deck_units_router.post(
    "/{deck_id}/units", response_model=DeckUnitOut, status_code=status.HTTP_201_CREATED
)
async def create_unit(
    deck_id: UUID,
    payload: DeckUnitCreateIn,
    current_user: CurrentUser,
    units: DeckUnitServiceDep,
) -> DeckUnitOut:
    unit = await units.create(deck_id, current_user.id, name=payload.name)
    return DeckUnitOut.model_validate(unit)


@units_router.patch("/{unit_id}", response_model=DeckUnitOut)
async def rename_unit(
    unit_id: UUID,
    payload: DeckUnitUpdateIn,
    current_user: CurrentUser,
    units: DeckUnitServiceDep,
) -> DeckUnitOut:
    unit = await units.rename(unit_id, current_user.id, name=payload.name)
    return DeckUnitOut.model_validate(unit)


@units_router.delete("/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    unit_id: UUID,
    current_user: CurrentUser,
    units: DeckUnitServiceDep,
) -> None:
    """Delete the unit only. Its cards stay in the deck, in no unit."""
    await units.delete(unit_id, current_user.id)
