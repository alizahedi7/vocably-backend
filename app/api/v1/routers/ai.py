"""AI Studio endpoints: meaning lookup, story generation, and rating a card back."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    AIStudioServiceDep,
    CurrentUser,
    FeedbackServiceDep,
    enforce_ai_feedback_limit,
)
from app.api.v1.schemas.ai import LookupIn, LookupOut, StoryOut
from app.api.v1.schemas.feedback import AIFeedbackIn, AIFeedbackOut
from app.domain.entities.feedback import AIRating

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/lookup", response_model=LookupOut)
async def look_up_meanings(
    payload: LookupIn,
    current_user: CurrentUser,
    ai: AIStudioServiceDep,
) -> LookupOut:
    """Suggest candidate meanings/senses for a word, in the user's native language.

    Powers the "AI Card Magic" deck: up to 4 card backs, each with a native-language
    meaning, a learner-dictionary definition, and contextual examples. ``status``
    reports how raw input was interpreted (typo corrected, keyword extracted from a
    sentence, translated from the native language, or unsupported).

    ``lookup_id`` names the deck for ``POST /ai/feedback``. It is empty when there
    is nothing to rate.
    """
    return LookupOut.from_dto(await ai.look_up_meanings(current_user.id, payload.term))


@router.post("/story", response_model=StoryOut)
async def generate_story(current_user: CurrentUser, ai: AIStudioServiceDep) -> StoryOut:
    """Generate a short practice story from the user's mastered words."""
    story = await ai.generate_story(current_user.id)
    return StoryOut.from_dto(story)


@router.post(
    "/feedback",
    response_model=AIFeedbackOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(enforce_ai_feedback_limit)],
)
async def rate_ai_sense(
    payload: AIFeedbackIn,
    current_user: CurrentUser,
    feedback: FeedbackServiceDep,
) -> AIFeedbackOut:
    """Rate one AI-written card back — or take a rating back.

    Sits here rather than under ``/feedback`` because it is the second half of
    ``/ai/lookup``: it takes that call's ``lookup_id`` and rates the cards it
    dealt, and no client has one endpoint without the other.

    **200, not 201, and idempotent.** A verdict is identified by
    ``(user, lookup_id, sense_index)``, so sending one twice moves a row rather
    than creating a second — which is what makes the client safe to fire this and
    forget it, and what keeps "how many people liked this card" a straight count.
    ``rating: "none"`` deletes the row and answers with ``rating: "none"``.
    """
    stored = await feedback.rate_ai_sense(
        current_user.id,
        lookup_id=payload.lookup_id,
        sense_index=payload.sense_index,
        rating=payload.rating,
        reason=payload.reason,
    )
    if stored is None:
        # Withdrawn: there is no row to describe, and the client's own control is
        # already showing an unrated card back. Echo the request so the answer is
        # still the same shape as every other one.
        return AIFeedbackOut(
            lookup_id=payload.lookup_id,
            sense_index=payload.sense_index,
            rating=AIRating.NONE,
        )
    return AIFeedbackOut(
        lookup_id=stored.lookup_id,
        sense_index=stored.sense_index,
        rating=stored.rating,
        reason=stored.reason,
    )
