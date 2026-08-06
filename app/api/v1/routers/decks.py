"""Deck CRUD endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DeckServiceDep
from app.api.v1.schemas.deck import (
    DeckCreateIn,
    DeckOut,
    DeckUpdateIn,
    DeckWithStatsOut,
)

router = APIRouter(prefix="/decks", tags=["decks"])


@router.get("", response_model=list[DeckWithStatsOut])
async def list_decks(current_user: CurrentUser, decks: DeckServiceDep) -> list[DeckWithStatsOut]:
    views = await decks.list_with_stats(current_user.id)
    return [DeckWithStatsOut.from_view(v) for v in views]


@router.post("", response_model=DeckOut, status_code=status.HTTP_201_CREATED)
async def create_deck(
    payload: DeckCreateIn,
    current_user: CurrentUser,
    decks: DeckServiceDep,
) -> DeckOut:
    deck = await decks.create(current_user.id, name=payload.name, hue=payload.hue)
    return DeckOut.model_validate(deck)


@router.get("/{deck_id}", response_model=DeckOut)
async def get_deck(
    deck_id: UUID,
    current_user: CurrentUser,
    decks: DeckServiceDep,
) -> DeckOut:
    deck = await decks.get_readable(deck_id, current_user.id)
    return DeckOut.model_validate(deck)


@router.patch("/{deck_id}", response_model=DeckOut)
async def update_deck(
    deck_id: UUID,
    payload: DeckUpdateIn,
    current_user: CurrentUser,
    decks: DeckServiceDep,
) -> DeckOut:
    deck = await decks.update(deck_id, current_user.id, name=payload.name, hue=payload.hue)
    return DeckOut.model_validate(deck)


@router.delete("/{deck_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deck(
    deck_id: UUID,
    current_user: CurrentUser,
    decks: DeckServiceDep,
) -> None:
    await decks.delete(deck_id, current_user.id)
