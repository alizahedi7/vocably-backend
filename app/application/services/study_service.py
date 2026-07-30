"""Study/review use cases: build the due queue, grade cards, compute home stats."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.dto import BoxCount, MemoryStrength, StudyOverview
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.domain.entities.review_event import ReviewEvent
from app.domain.entities.word import Word
from app.domain.enums import LeitnerBox, ReviewGrade
from app.domain.repositories.review_event_repository import ReviewEventRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_repository import WordRepository
from app.domain.services import leitner

#: Rough estimate used for the "~N min" hint on the home screen.
_MINUTES_PER_CARD = 0.5
#: Minimum mastered words before story generation is meaningful.
LEARNED_BOXES = (LeitnerBox.KNOWN, LeitnerBox.MASTERED)


class StudyService:
    def __init__(
        self,
        words: WordRepository,
        users: UserRepository,
        reviews: ReviewEventRepository,
    ) -> None:
        self._words = words
        self._users = users
        self._reviews = reviews

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
        """Return the queue of cards for a study session.

        Due cards take priority; if nothing is due (the deck, or the whole
        account, is fully caught up), fall back to the full word list so
        "practice anyway" sessions always have something to show.
        """
        now = datetime.now(UTC)
        due = await self._words.list_due(user_id, now, deck_id=deck_id, limit=limit)
        if due:
            return due
        return await self._words.list_for_user(user_id, deck_id=deck_id, limit=limit)

    async def grade(
        self,
        user_id: UUID,
        word_id: UUID,
        grade: ReviewGrade,
        *,
        latency_ms: int | None = None,
        session_id: UUID | None = None,
    ) -> Word:
        """Apply a review grade to a card, log the review, and advance the streak."""
        word = await self._words.get(word_id)
        if word is None:
            raise NotFoundError("Word not found.")
        if word.user_id != user_id:
            raise PermissionDeniedError("This word belongs to another user.")

        now = datetime.now(UTC)
        outcome = leitner.review(word.box, grade, now)

        # Built before apply_review, which overwrites the pre-review box, due
        # date and last-reviewed time the event needs.
        event = ReviewEvent.from_review(
            word,
            grade,
            outcome.box,
            now,
            latency_ms=latency_ms,
            session_id=session_id,
        )
        word.apply_review(grade, outcome.box, outcome.due_at, now)
        updated = await self._words.update(word)

        # Written in the same transaction as the card update, unlike the AI
        # lookup cache's best-effort writes. That cache is derived data — a lost
        # write costs one repeat API call. A lost review is gone for good and
        # leaves the log disagreeing with review_count, so a failure here must
        # fail the request and let the client retry.
        await self._reviews.add(event)

        user = await self._users.get(user_id)
        if user is not None:
            user.register_study_day(now.date())
            await self._users.update(user)

        return updated
