"""AI Studio endpoints: meaning lookup and story generation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AIStudioServiceDep, CurrentUser
from app.api.v1.schemas.ai import LookupIn, LookupOut, MeaningSuggestionOut, StoryOut

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/lookup", response_model=LookupOut)
async def look_up_meanings(
    payload: LookupIn,
    current_user: CurrentUser,
    ai: AIStudioServiceDep,
) -> LookupOut:
    """Suggest candidate meanings/senses for a word, in the user's native language."""
    suggestions = await ai.look_up_meanings(current_user.id, payload.term)
    return LookupOut(
        term=payload.term,
        suggestions=[MeaningSuggestionOut.from_dto(s) for s in suggestions],
    )


@router.post("/story", response_model=StoryOut)
async def generate_story(current_user: CurrentUser, ai: AIStudioServiceDep) -> StoryOut:
    """Generate a short practice story from the user's mastered words."""
    story = await ai.generate_story(current_user.id)
    return StoryOut.from_dto(story)
