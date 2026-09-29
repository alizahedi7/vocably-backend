"""The library: public-domain books a learner can read."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, ReaderServiceDep
from app.api.v1.schemas.reader import BookDetailOut, BookOut, BookPageOut, ChapterOut

router = APIRouter(prefix="/books", tags=["books"])


@router.get("", response_model=BookPageOut)
async def list_books(
    current_user: CurrentUser,
    reader: ReaderServiceDep,
    language: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookPageOut:
    items, total = await reader.list_books(language=language, limit=limit, offset=offset)
    return BookPageOut(items=[BookOut.model_validate(b) for b in items], total=total)


@router.get("/{book_id}", response_model=BookDetailOut)
async def get_book(
    book_id: UUID, current_user: CurrentUser, reader: ReaderServiceDep
) -> BookDetailOut:
    book, position = await reader.get_book(book_id, current_user)
    return BookDetailOut.from_book(book, position)


@router.get("/{book_id}/chapters/{chapter_id}", response_model=ChapterOut)
async def get_chapter(
    book_id: UUID,
    chapter_id: UUID,
    current_user: CurrentUser,
    reader: ReaderServiceDep,
    # A chapter is typically 15-80 blocks, so the default returns it whole.
    # ``after`` is a block *position*, not a row offset: positions are the
    # stored order and never shift.
    after: Annotated[int, Query(ge=-1, description="Return blocks after this position")] = -1,
    limit: Annotated[int, Query(ge=1, le=400)] = 400,
) -> ChapterOut:
    chapter = await reader.get_chapter(book_id, chapter_id, current_user, after=after, limit=limit)
    return ChapterOut.from_chapter(chapter)
