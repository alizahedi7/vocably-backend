"""AI Studio endpoints: meaning lookup and story generation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AIStudioServiceDep, CurrentUser
from app.api.v1.schemas.ai import LookupIn, LookupOut, StoryOut

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
    """
    return LookupOut.from_dto(await ai.look_up_meanings(current_user.id, payload.term))


@router.post("/story", response_model=StoryOut)
async def generate_story(current_user: CurrentUser, ai: AIStudioServiceDep) -> StoryOut:
    """Generate a short practice story from the user's mastered words."""
    story = await ai.generate_story(current_user.id)
    return StoryOut.from_dto(story)
