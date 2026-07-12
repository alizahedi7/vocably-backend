"""Study/review use cases: build the due queue, grade cards, compute home stats."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.dto import BoxCount, MemoryStrength, StudyOverview
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.domain.entities.word import Word
from app.domain.enums import LeitnerBox, ReviewGrade
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_repository import WordRepository
from app.domain.services import leitner

#: Rough estimate used for the "~N min" hint on the home screen.
_MINUTES_PER_CARD = 0.5
#: Minimum mastered words before story generation is meaningful.
LEARNED_BOXES = (LeitnerBox.KNOWN, LeitnerBox.MASTERED)


class StudyService:
    def __init__(self, words: WordRepository, users: UserRepository) -> None:
        self._words = words
        self._users = users

    async def get_overview(self, user_id: UUID) -> StudyOverview:
        now = datetime.now(UTC)
        distribution = await self._words.box_distribution(user_id)
        total = sum(distribution.values())
        learned = sum(distribution.get(box, 0) for box in LEARNED_BOXES)

        due_words = await self._words.list_due(user_id, now)
        due_count = len(due_words)
        due_deck_count = len({w.deck_id for w in due_words})

        memory = MemoryStrength(
            total=total,
            distribution=[
                BoxCount(box=box, label=box.label, count=distribution.get(box, 0))
                for box in LeitnerBox
            ],
        )
        user = await self._users.get(user_id)
        return StudyOverview(
            due_count=due_count,
            total_count=total,
            learned_count=learned,
            due_deck_count=due_deck_count,
            estimated_minutes=max(1, round(due_count * _MINUTES_PER_CARD)),
            streak=user.streak if user else 0,
            memory_strength=memory,
        )

    async def build_session(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Word]:
        """Return the queue of due cards for a study session."""
        now = datetime.now(UTC)
        return await self._words.list_due(user_id, now, deck_id=deck_id, limit=limit)

    async def grade(self, user_id: UUID, word_id: UUID, grade: ReviewGrade) -> Word:
        """Apply a review grade to a card and advance the user's streak."""
        word = await self._words.get(word_id)
        if word is None:
            raise NotFoundError("Word not found.")
        if word.user_id != user_id:
            raise PermissionDeniedError("This word belongs to another user.")

        now = datetime.now(UTC)
        outcome = leitner.review(word.box, grade, now)
        word.box = outcome.box
        word.due_at = outcome.due_at
        word.review_count += 1
        word.last_reviewed_at = now
        word.updated_at = now
        updated = await self._words.update(word)

        user = await self._users.get(user_id)
        if user is not None:
            user.register_study_day(now.date())
            await self._users.update(user)

        return updated
