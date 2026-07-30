"""Study/review endpoints: home overview, session queue, grading."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, StudyServiceDep
from app.api.v1.schemas.study import GradeIn, StudyOverviewOut, StudySessionOut
from app.api.v1.schemas.word import WordOut

router = APIRouter(prefix="/study", tags=["study"])


@router.get("/overview", response_model=StudyOverviewOut)
async def get_overview(current_user: CurrentUser, study: StudyServiceDep) -> StudyOverviewOut:
    """Home-screen data: due counts, streak, and memory-strength distribution."""
    overview = await study.get_overview(current_user.id)
    return StudyOverviewOut.from_dto(overview)


@router.get("/session", response_model=StudySessionOut)
async def build_session(
    current_user: CurrentUser,
    study: StudyServiceDep,
    deck_id: Annotated[UUID | None, Query(description="Limit to a single deck")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> StudySessionOut:
    words = await study.build_session(current_user.id, deck_id=deck_id, limit=limit)
    return StudySessionOut(
        count=len(words),
        words=[WordOut.model_validate(w) for w in words],
    )


@router.post("/words/{word_id}/grade", response_model=WordOut)
async def grade_word(
    word_id: UUID,
    payload: GradeIn,
    current_user: CurrentUser,
    study: StudyServiceDep,
) -> WordOut:
    """Grade a reviewed card; advances its Leitner box and the study streak.

    Also appends an immutable row to the review log. The extra ``latency_ms``
    and ``session_id`` fields are optional, so clients predating them keep
    working unchanged.
    """
    word = await study.grade(
        current_user.id,
        word_id,
        payload.grade,
        latency_ms=payload.latency_ms,
        session_id=payload.session_id,
    )
    return WordOut.model_validate(word)
