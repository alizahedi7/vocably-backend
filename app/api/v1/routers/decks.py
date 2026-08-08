"""Deck CRUD endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DeckServiceDep, WordServiceDep
from app.api.v1.schemas.deck import (
    DeckCreateIn,
    DeckOut,
    DeckUpdateIn,
    DeckWithStatsOut,
)
from app.api.v1.schemas.word import StartResultOut, StartWordsIn, UnstartWordsIn

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


@router.post("/{deck_id}/start", response_model=StartResultOut)
async def start_words(
    deck_id: UUID,
    payload: StartWordsIn,
    current_user: CurrentUser,
    words: WordServiceDep,
) -> StartResultOut:
    """Put some of a saved deck's cards into the caller's own boxes.

    Saving "504 Essential Words" hands over five hundred cards; this is how
    they enter a review queue — a unit, a batch of ten, or one card — instead
    of all at once. Idempotent, and safe for any member: what it writes is the
    caller's own progress, which nobody else can see.
    """
    result = await words.start(
        current_user.id,
        deck_id,
        word_ids=payload.word_ids,
        unit_id=payload.unit_id,
        count=payload.count,
    )
    return StartResultOut(
        started_ids=result.started_ids, started=result.started, remaining=result.remaining
    )


@router.post("/{deck_id}/unstart", response_model=StartResultOut)
async def unstart_words(
    deck_id: UUID,
    payload: UnstartWordsIn,
    current_user: CurrentUser,
    words: WordServiceDep,
) -> StartResultOut:
    """Undo a start. Cards already answered keep their progress and stay."""
    result = await words.unstart(current_user.id, deck_id, payload.word_ids)
    return StartResultOut(
        started_ids=result.started_ids, started=result.started, remaining=result.remaining
    )
