"""AI Studio use cases: meaning lookup and story generation.

Business rules live here (e.g. which words feed a story, minimum thresholds); the actual
model call is delegated to the :class:`AIService` port.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from app.application.ports.ai_service import (
    AIService,
    GeneratedStory,
    LearnerContext,
    LookupResult,
)
from app.core.exceptions import NotFoundError, ValidationError
from app.domain.enums import LeitnerBox
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_progress_repository import WordProgressRepository

#: Minimum learned words required before a story can be generated.
MIN_WORDS_FOR_STORY = 3
#: Words in these boxes are considered "learned" and eligible for stories.
STORY_ELIGIBLE_BOXES = (LeitnerBox.KNOWN, LeitnerBox.MASTERED)
#: Cap how many words we feed the model to keep prompts tight.
MAX_STORY_WORDS = 12
#: The "AI Card Magic" deck renders at most this many card backs.
MAX_LOOKUP_SUGGESTIONS = 4
#: Longest raw input accepted for a lookup — a word, phrase, idiom, or short sentence.
MAX_LOOKUP_CHARS = 200


class AIStudioService:
    def __init__(
        self,
        ai: AIService,
        progress: WordProgressRepository,
        users: UserRepository,
    ) -> None:
        self._ai = ai
        self._progress = progress
        self._users = users

    async def look_up_meanings(
        self,
        user_id: UUID,
        term: str,
    ) -> LookupResult:
        term = term.strip()
        if not term:
            raise ValidationError("A term is required.")
        if len(term) > MAX_LOOKUP_CHARS:
            # A cost/abuse backstop: the endpoint takes a word, phrase, or short
            # sentence — anything longer is pasted prose, not a lookup.
            raise ValidationError(
                f"Keep the lookup under {MAX_LOOKUP_CHARS} characters — "
                "enter a word, phrase, or short sentence."
            )
        learner = await self._learner_context(user_id)
        result = await self._ai.look_up_meanings(term, learner)
        # The card deck shows at most 4 backs; enforce it here so no provider can
        # push the client past what the UI can render.
        if len(result.suggestions) > MAX_LOOKUP_SUGGESTIONS:
            return replace(result, suggestions=result.suggestions[:MAX_LOOKUP_SUGGESTIONS])
        return result

    async def generate_story(self, user_id: UUID) -> GeneratedStory:
        learner = await self._learner_context(user_id)
        # The learner's own boxes, not the deck's: a story is written from
        # what *this* person has learned, which in a shared deck is not what
        # anyone else has.
        words = await self._progress.list_for_user(user_id)
        learned = [w.term for w in words if w.box in STORY_ELIGIBLE_BOXES]

        if len(learned) < MIN_WORDS_FOR_STORY:
            raise ValidationError(
                f"Learn at least {MIN_WORDS_FOR_STORY} words before generating a story."
            )
        return await self._ai.generate_story(learned[:MAX_STORY_WORDS], learner)

    async def _learner_context(self, user_id: UUID) -> LearnerContext:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return LearnerContext(
            native_language=user.native_language,
            age_range=user.age_range.value if user.age_range else None,
            interests=tuple(user.interests),
        )
