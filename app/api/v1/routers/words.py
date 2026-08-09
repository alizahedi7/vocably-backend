"""Word (flashcard) CRUD endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, WordServiceDep
from app.api.v1.schemas.word import WordCreateIn, WordOut, WordUpdateIn
from app.application.services.word_service import UNSET

router = APIRouter(prefix="/words", tags=["words"])


@router.get("", response_model=list[WordOut])
async def list_words(
    current_user: CurrentUser,
    words: WordServiceDep,
    deck_id: Annotated[UUID | None, Query(description="Filter by deck")] = None,
    # The ceiling has to clear the largest deck a learner can hold, because a
    # client that asks for the maximum and does not paginate gets a silently
    # truncated deck: no `total` comes back, so nothing downstream can tell a
    # short page from a small deck. At 500 the 504-word "504 Essential Words"
    # lost its four oldest cards — the whole of Lesson 1's opening — and the
    # only symptom was a lesson showing 8 words. "1100 Words You Need to Know"
    # would have lost six hundred.
    limit: Annotated[int, Query(ge=1, le=2000, description="Page size")] = 100,
    offset: Annotated[int, Query(ge=0, description="Rows to skip")] = 0,
) -> list[WordOut]:
    items = await words.list_words(current_user.id, deck_id=deck_id, limit=limit, offset=offset)
    return [WordOut.model_validate(w) for w in items]


@router.post("", response_model=WordOut, status_code=status.HTTP_201_CREATED)
async def create_word(
    payload: WordCreateIn,
    current_user: CurrentUser,
    words: WordServiceDep,
) -> WordOut:
    word = await words.create(
        current_user.id,
        deck_id=payload.deck_id,
        term=payload.term,
        meaning=payload.meaning,
        definition=payload.definition,
        example=payload.example,
        sense_label=payload.sense_label,
        phonetic=payload.phonetic,
        unit_id=payload.unit_id,
    )
    return WordOut.model_validate(word)


@router.get("/{word_id}", response_model=WordOut)
async def get_word(
    word_id: UUID,
    current_user: CurrentUser,
    words: WordServiceDep,
) -> WordOut:
    word = await words.get_readable(word_id, current_user.id)
    return WordOut.model_validate(word)


@router.patch("/{word_id}", response_model=WordOut)
async def update_word(
    word_id: UUID,
    payload: WordUpdateIn,
    current_user: CurrentUser,
    words: WordServiceDep,
) -> WordOut:
    word = await words.update(
        word_id,
        current_user.id,
        term=payload.term,
        meaning=payload.meaning,
        definition=payload.definition,
        example=payload.example,
        sense_label=payload.sense_label,
        phonetic=payload.phonetic,
        deck_id=payload.deck_id,
        # model_fields_set, not `is None`: an omitted unit_id means "leave it
        # alone" while an explicit null means "take it out of its unit", and
        # the client sends both.
        unit_id=(payload.unit_id if "unit_id" in payload.model_fields_set else UNSET),
    )
    return WordOut.model_validate(word)


@router.delete("/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_word(
    word_id: UUID,
    current_user: CurrentUser,
    words: WordServiceDep,
) -> None:
    await words.delete(word_id, current_user.id)
