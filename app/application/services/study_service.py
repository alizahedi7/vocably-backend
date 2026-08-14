"""Study/review use cases: build the due queue, grade cards, compute home stats."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from app.application.dto import BoxCount, MemoryStrength, StudyOverview
from app.core.exceptions import NotFoundError
from app.domain.entities.review_event import ReviewEvent
from app.domain.entities.studied_word import StudiedWord
from app.domain.entities.xp import XpAction
from app.domain.enums import LeitnerBox, ReviewGrade
from app.domain.repositories.deck_activity_repository import DeckActivityRepository
from app.domain.repositories.review_event_repository import ReviewEventRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_progress_repository import WordProgressRepository
from app.domain.repositories.xp_repository import XpRepository
from app.domain.services import leitner
from app.domain.services.calendar import day_end_for, day_start_for, today_for

#: Where a graded answer came from. A review session and a practice drill
#: are worth different amounts, and the client says which.
ReviewSource = Literal["session", "drill"]

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
        xp: XpRepository,
    ) -> None:
        self._progress = progress
        self._users = users
        self._reviews = reviews
        self._activity = activity
        self._xp = xp

    async def get_overview(self, user_id: UUID) -> StudyOverview:
        """Every home-screen number, from one grouped query.

        This used to fetch every due row and count them in Python, which made
        the home screen cost one row per due card. The response is unchanged —
        deliberately, so the migration that moved these columns can be proved
        against it byte for byte.
        """
        now = datetime.now(UTC)
        user = await self._users.get(user_id)
        # Where *this* learner's day starts and ends. The pair is what makes
        # every number below a fact about the day rather than about the moment
        # it was asked for: a word added today is tomorrow's work, and a card
        # already scheduled for today is due from midnight, not from whatever
        # o'clock it happened to be graded at.
        timezone = user.timezone if user else None
        day_start = day_start_for(timezone, now)
        day_end = day_end_for(timezone, now)
        tallies = await self._progress.tally_by_deck_and_box(
            user_id, now, day_start=day_start, day_end=day_end
        )

        per_box: dict[LeitnerBox, int] = defaultdict(int)
        total = due_count = learned = 0
        due_decks: set[UUID] = set()
        for tally in tallies:
            # A card in a self-paced deck that nobody has started is not part of
            # this learner's memory at all — counting it would put five hundred
            # bars in box 1 and read as "you have forgotten everything".
            if not tally.started:
                continue
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
        today = today_for(timezone, now)
        return StudyOverview(
            due_count=due_count,
            total_count=total,
            learned_count=learned,
            mastered_count=per_box.get(LeitnerBox.MASTERED, 0),
            # From the rollup, so it costs a handful of indexed rows rather
            # than an aggregate over the review log.
            reviewed_today=await self._activity.reviews_on(user_id, today),
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
        user = await self._users.get(user_id)
        timezone = user.timezone if user else None
        day_start = day_start_for(timezone, now)
        due = await self._progress.list_due(
            user_id,
            now,
            deck_id=deck_id,
            limit=limit,
            day_start=day_start,
            day_end=day_end_for(timezone, now),
        )
        if due:
            return due
        # Nothing is due — which now includes the evening a learner adds their
        # first words, since those are tomorrow's. "Practice anyway" is the
        # answer to that: it practises what they have taken on, never the four
        # hundred cards they have not started.
        return await self._progress.list_for_user(
            user_id, deck_id=deck_id, limit=limit, started_only=True, day_start=day_start
        )

    async def grade(
        self,
        user_id: UUID,
        word_id: UUID,
        grade: ReviewGrade,
        *,
        latency_ms: int | None = None,
        session_id: UUID | None = None,
        source: ReviewSource = "session",
    ) -> StudiedWord:
        """Apply a review grade to a card, log the review, and advance the streak.

        ``source`` decides what the answer is worth: a practice drill pays
        differently from a review session, and a *wrong* drill answer still
        pays — turning up to be tested on your weakest words is the behaviour
        worth rewarding.
        """
        # Grading a card you can read is always allowed — study is not an edit,
        # so a viewer in a class deck may study it like anyone else. A
        # non-member gets 404: a 403 would confirm the card exists.
        studied = await self._progress.get_for_user(word_id, user_id)
        if studied is None:
            raise NotFoundError("Word not found.")

        now = datetime.now(UTC)
        # Fetched before the review rather than after it: the next due date is
        # anchored to the start of *this learner's* day, so their timezone is an
        # input to the scheduling and not just to the streak below.
        user = await self._users.get(user_id)
        timezone = user.timezone if user else None
        outcome = leitner.review(studied.box, grade, now, day_start_for(timezone, now))

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

        # The learner's own day, not UTC: a review at 01:00 in Tehran belongs to
        # that day, and the streak and the roster's week must agree about which.
        today = today_for(timezone, now)
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

        await self._award_for_grade(user_id, grade, source, today, now)
        return studied

    async def _award_for_grade(
        self,
        user_id: UUID,
        grade: ReviewGrade,
        source: ReviewSource,
        today: date,
        now: datetime,
    ) -> None:
        if source == "drill":
            action = XpAction.DRILL_WRONG if grade.is_lapse else XpAction.DRILL_CORRECT
        else:
            action = XpAction.GRADE_WORD
        await self._xp.award(user_id, action, occurred_at=now, day=today)
        await self._award_daily_goal_if_met(user_id, today, now)

    async def _award_daily_goal_if_met(self, user_id: UUID, today: date, now: datetime) -> None:
        """Pay the daily goal the first time it is met, and only then.

        Derived from the rollup rather than trusted from the client: an
        endpoint that accepts "I met my goal" is an endpoint that hands out
        25 points on request. The once-a-day guarantee is a partial unique
        index, not a check here, so two sessions finishing together cannot
        both collect.
        """
        user = await self._users.get(user_id)
        if user is None:  # pragma: no cover
            return
        if await self._activity.reviews_on(user_id, today) < user.daily_goal:
            return
        await self._xp.award(
            user_id, XpAction.DAILY_GOAL, occurred_at=now, day=today, once_per_day=True
        )

    async def complete_session(self, user_id: UUID) -> int:
        """Award the end-of-session bonus, on top of the cards.

        Separate from grading because finishing is its own act: the client
        calls it when the last card of a queue is answered, and a session
        abandoned halfway earns the cards but not the bonus.
        """
        now = datetime.now(UTC)
        user = await self._users.get(user_id)
        today = today_for(user.timezone if user else None, now)
        awarded = await self._xp.award(user_id, XpAction.FINISH_SESSION, occurred_at=now, day=today)
        await self._award_daily_goal_if_met(user_id, today, now)
        return awarded
