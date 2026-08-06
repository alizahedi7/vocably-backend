"""Study/review use cases: build the due queue, grade cards, compute home stats."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from app.application.dto import BoxCount, MemoryStrength, StudyOverview
from app.core.exceptions import NotFoundError
from app.domain.entities.review_event import ReviewEvent
from app.domain.entities.studied_word import StudiedWord
from app.domain.enums import LeitnerBox, ReviewGrade
from app.domain.repositories.deck_activity_repository import DeckActivityRepository
from app.domain.repositories.review_event_repository import ReviewEventRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_progress_repository import WordProgressRepository
from app.domain.services import leitner
from app.domain.services.calendar import today_for

#: Rough estimate used for the "~N min" hint on the home screen.
_MINUTES_PER_CARD = 0.5
#: Minimum mastered words before story generation is meaningful.
LEARNED_BOXES = (LeitnerBox.KNOWN, LeitnerBox.MASTERED)


class StudyService:
    def __init__(
        self,
        progress: WordProgressRepository,
        users: UserRepository,
        reviews: ReviewEventRepository,
        activity: DeckActivityRepository,
    ) -> None:
        self._progress = progress
        self._users = users
        self._reviews = reviews
        self._activity = activity

    async def get_overview(self, user_id: UUID) -> StudyOverview:
        """Every home-screen number, from one grouped query.

        This used to fetch every due row and count them in Python, which made
        the home screen cost one row per due card. The response is unchanged —
        deliberately, so the migration that moved these columns can be proved
        against it byte for byte.
        """
        now = datetime.now(UTC)
        tallies = await self._progress.tally_by_deck_and_box(user_id, now)

        per_box: dict[LeitnerBox, int] = defaultdict(int)
        total = due_count = learned = 0
        due_decks: set[UUID] = set()
        for tally in tallies:
            per_box[tally.box] += tally.word_count
            total += tally.word_count
            due_count += tally.due_count
            if tally.box in LEARNED_BOXES:
                learned += tally.word_count
            if tally.due_count:
                due_decks.add(tally.deck_id)

        memory = MemoryStrength(
            total=total,
            # Every box, including the empty ones: the client renders five bars
            # and hard-casts each one.
            distribution=[
                BoxCount(box=box, label=box.label, count=per_box.get(box, 0)) for box in LeitnerBox
            ],
        )
        user = await self._users.get(user_id)
        return StudyOverview(
            due_count=due_count,
            total_count=total,
            learned_count=learned,
            due_deck_count=len(due_decks),
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
    ) -> list[StudiedWord]:
        """Return the queue of cards for a study session.

        Due cards take priority; if nothing is due (the deck, or the whole
        account, is fully caught up), fall back to the full word list so
        "practice anyway" sessions always have something to show.
        """
        now = datetime.now(UTC)
        due = await self._progress.list_due(user_id, now, deck_id=deck_id, limit=limit)
        if due:
            return due
        return await self._progress.list_for_user(user_id, deck_id=deck_id, limit=limit)

    async def grade(
        self,
        user_id: UUID,
        word_id: UUID,
        grade: ReviewGrade,
        *,
        latency_ms: int | None = None,
        session_id: UUID | None = None,
    ) -> StudiedWord:
        """Apply a review grade to a card, log the review, and advance the streak."""
        # Grading a card you can read is always allowed — study is not an edit,
        # so a viewer in a class deck may study it like anyone else. A
        # non-member gets 404: a 403 would confirm the card exists.
        studied = await self._progress.get_for_user(word_id, user_id)
        if studied is None:
            raise NotFoundError("Word not found.")

        now = datetime.now(UTC)
        outcome = leitner.review(studied.box, grade, now)

        # Built before apply_review, which overwrites the pre-review box, due
        # date and last-reviewed time the event needs.
        event = ReviewEvent.from_review(
            studied,
            grade,
            outcome.box,
            now,
            latency_ms=latency_ms,
            session_id=session_id,
        )
        applied = studied.progress.apply_review(grade, outcome.box, outcome.due_at, now)
        # Returns the row that actually landed, which under two concurrent
        # grades of the same card is not the one computed above: the counters
        # are incremented in SQL so neither review is lost.
        studied = StudiedWord(
            word=studied.word,
            progress=await self._progress.record_grade(studied.progress, is_lapse=grade.is_lapse),
        )

        # Written in the same transaction as the progress update, unlike the AI
        # lookup cache's best-effort writes. That cache is derived data — a lost
        # write costs one repeat API call. A lost review is gone for good and
        # leaves the log disagreeing with review_count, so a failure here must
        # fail the request and let the client retry.
        await self._reviews.add(event)

        user = await self._users.get(user_id)
        # The learner's own day, not UTC: a review at 01:00 in Tehran belongs to
        # that day, and the streak and the roster's week must agree about which.
        today = today_for(user.timezone if user else None, now)
        # Rides along on the same transaction. This is what keeps the roster off
        # word_reviews, which CLAUDE.md forbids aggregating for a user-facing
        # request — a roster of thirty students would otherwise scan it thirty
        # times.
        await self._activity.record_review(
            user_id, studied.deck_id, today, mastered=applied.became_mastered
        )
        if user is not None:
            user.register_study_day(today)
            await self._users.update(user)

        return studied
