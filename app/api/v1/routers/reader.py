"""What a learner does while reading: look up, translate, and keep their place."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    CurrentUser,
    ReaderServiceDep,
    enforce_passage_translation_limit,
    enforce_reader_lookup_limit,
)
from app.api.v1.schemas.reader import (
    LookupWordIn,
    LookupWordOut,
    ProgressIn,
    ReadingPositionOut,
    TranslateParagraphIn,
    TranslateParagraphOut,
)

router = APIRouter(prefix="/reader", tags=["reader"])


@router.post(
    "/lookup-word",
    response_model=LookupWordOut,
    dependencies=[Depends(enforce_reader_lookup_limit)],
)
async def look_up_word(
    payload: LookupWordIn, current_user: CurrentUser, reader: ReaderServiceDep
) -> LookupWordOut:
    """The tapped word's senses, with the one this sentence uses marked.

    ``lookup.lookup_id`` is the id ``POST /ai/lookup`` would return for the
    lemma, so a thumb on a reader card goes to ``POST /ai/feedback`` unchanged.
    """
    view = await reader.look_up(
        current_user,
        word=payload.word,
        sentence=payload.sentence_context,
        block_id=payload.block_id,
        char_start=payload.char_start,
        char_end=payload.char_end,
    )
    return LookupWordOut.from_view(view)


@router.post(
    "/translate-paragraph",
    response_model=TranslateParagraphOut,
    dependencies=[Depends(enforce_passage_translation_limit)],
)
async def translate_paragraph(
    payload: TranslateParagraphIn, current_user: CurrentUser, reader: ReaderServiceDep
) -> TranslateParagraphOut:
    view = await reader.translate(
        current_user,
        block_id=payload.block_id,
        text=payload.paragraph_text,
        target_language=payload.target_language,
    )
    return TranslateParagraphOut.from_view(view)


@router.post("/progress", response_model=ReadingPositionOut)
async def sync_progress(
    payload: ProgressIn, current_user: CurrentUser, reader: ReaderServiceDep
) -> ReadingPositionOut:
    """Upsert. ``percent`` is computed here from word counts, never accepted."""
    position = await reader.sync_position(
        current_user,
        book_id=payload.book_id,
        block_id=payload.block_id,
        char_offset=payload.char_offset,
    )
    return ReadingPositionOut.model_validate(position)


@router.get("/progress", response_model=list[ReadingPositionOut])
async def shelf(current_user: CurrentUser, reader: ReaderServiceDep) -> list[ReadingPositionOut]:
    """The "Continue reading" shelf, most recently touched first."""
    return [ReadingPositionOut.model_validate(p) for p in await reader.shelf(current_user)]


@router.delete("/progress/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_shelf(
    book_id: UUID, current_user: CurrentUser, reader: ReaderServiceDep
) -> None:
    await reader.remove_from_shelf(current_user, book_id)
