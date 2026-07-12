"""AI Studio use cases: meaning lookup and story generation.

Business rules live here (e.g. which words feed a story, minimum thresholds); the actual
model call is delegated to the :class:`AIService` port.
"""

from __future__ import annotations

from uuid import UUID

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    MeaningSuggestion,
)
from app.core.exceptions import NotFoundError, ValidationError
from app.domain.enums import LeitnerBox
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_repository import WordRepository

#: Minimum learned words required before a story can be generated.
MIN_WORDS_FOR_STORY = 3
#: Words in these boxes are considered "learned" and eligible for stories.
STORY_ELIGIBLE_BOXES = (LeitnerBox.KNOWN, LeitnerBox.MASTERED)
#: Cap how many words we feed the model to keep prompts tight.
MAX_STORY_WORDS = 12


class AIStudioService:
    def __init__(
        self,
        ai: AIService,
        words: WordRepository,
        users: UserRepository,
    ) -> None:
        self._ai = ai
        self._words = words
        self._users = users

    async def look_up_meanings(
        self,
        user_id: UUID,
        term: str,
    ) -> list[MeaningSuggestion]:
        term = term.strip()
        if not term:
            raise ValidationError("A term is required.")
        native_language = await self._native_language(user_id)
        return await self._ai.look_up_meanings(term, native_language)

    async def generate_story(self, user_id: UUID) -> GeneratedStory:
        native_language = await self._native_language(user_id)
        words = await self._words.list_for_user(user_id)
        learned = [w.term for w in words if w.box in STORY_ELIGIBLE_BOXES]

        if len(learned) < MIN_WORDS_FOR_STORY:
            raise ValidationError(
                f"Learn at least {MIN_WORDS_FOR_STORY} words before generating a story."
            )
        return await self._ai.generate_story(learned[:MAX_STORY_WORDS], native_language)

    async def _native_language(self, user_id: UUID) -> str:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user.native_language
