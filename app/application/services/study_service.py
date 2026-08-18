"""Study/review use cases: build the due queue, grade cards, compute home stats."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from app.application.dto import BoxCount, MemoryStrength, StudyOverview
from app.core.exceptions import NotFoundError
from app.domain.entities.review_event import ReviewEvent
from app.domain.entities.studied_word import StudiedWord
from app.domain.entities.user import User
from app.domain.entities.xp import XpAction
from app.domain.enums import LeitnerBox, ReviewGrade
from app.domain.repositories.deck_activity_repository import DeckActivityRepository
from app.domain.repositories.review_event_repository import ReviewEventRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.word_progress_repository import WordProgressRepository
from app.domain.repositories.xp_repository import XpRepository
from app.domain.services import leitner
from app.domain.services import streak as streak_rules
from app.domain.services.calendar import day_end_for, day_start_for, today_for
from app.domain.services.streak import DayState

#: Where a graded answer came from. A review session and a practice drill
#: are worth different amounts, and the client says which.
ReviewSource = Literal["session", "drill"]


class CelebrationClaim(StrEnum):
    """Who, if anyone, gets to show today's "goal reached" celebration."""

    #: Yours. Exactly one request per account per local day is answered this.
    CLAIMED = "claimed"
    #: The day was won, and somebody has already been told. Stop asking today.
    TAKEN = "taken"
    #: Nothing has been won yet. Ask again after the next refresh.
    UNBANKED = "unbanked"


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
        # From the rollup, so it costs a handful of indexed rows rather than an
        # aggregate over the review log.
        totals = await self._activity.totals_on(user_id, today)
        settled, day_state = await self._settle_on_read(user, today, due_count)
        return StudyOverview(
            due_count=due_count,
            total_count=total,
            learned_count=learned,
            mastered_count=per_box.get(LeitnerBox.MASTERED, 0),
            reviewed_today=totals.reviews,
            due_deck_count=len(due_decks),
            estimated_minutes=max(1, round(due_count * _MINUTES_PER_CARD)),
            streak=settled,
            daily_goal=user.daily_goal if user else 0,
            day_state=day_state,
            memory_strength=memory,
        )

    async def _settle_on_read(
        self, user: User | None, today: date, due_count: int
    ) -> tuple[int, DayState]:
        """The streak as it actually stands, and how today reads.

        A streak dies while nobody is looking, so this is the moment it is
        brought up to date: without it a learner who missed two days keeps
        seeing the old number until their next review silently resets it to 1.

        The write is narrow and happens at most once a day — a settled value
        equal to the stored one issues nothing, which is every refresh after
        the first.
        """
        if user is None:  # pragma: no cover
            return 0, DayState.OPEN
        stored = user.streak_state
        settled = streak_rules.settle(stored, today, due_count)
        if settled != stored:
            await self._users.settle_streak(user.id, days=settled.days, last_day=settled.last_day)
        return settled.days, streak_rules.state_of(settled, today, due_count)

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
        # Read before apply_review for the same reason the event below is: this
        # is a fact about the card as the learner met it. A card nobody has
        # started is in no queue at all, so answering one is practice, not the
        # day's scheduled work.
        was_due = studied.started and studied.progress.is_due(day_end_for(timezone, now))

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
            user_id,
            studied.deck_id,
            today,
            mastered=applied.became_mastered,
            # Whether the scheduler had actually asked for this card, judged
            # against the pre-review row — `apply_review` above has already
            # moved `due_at` to the next interval, so asking afterwards would
            # answer about tomorrow.
            was_due=was_due,
        )
        if user is not None and user.note_study_day(today):
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
        await self._settle_day(user_id, today, now)

    async def _goal_met(self, user: User, today: date, now: datetime) -> bool:
        """Whether the learner has done the day's work.

        Two ways, and the second is what makes a light day winnable: either
        they answered ``daily_goal`` cards, or the queue the scheduler gave
        them is empty and they were the ones who emptied it. A learner with
        four cards due and a goal of ten has finished; making them over-review
        to hold a streak is how a streak stops meaning anything.

        Both are derived from the rollup, never claimed by the client — an
        endpoint that accepts "I met my goal" is an endpoint that hands out a
        streak on request.
        """
        totals = await self._activity.totals_on(user.id, today)
        if totals.reviews == 0:
            return False
        if user.daily_goal > 0 and totals.reviews >= user.daily_goal:
            return True
        if totals.due_reviews == 0:
            # Nothing scheduled was answered, so there is no queue to have
            # cleared. This is the guard that stops a brand-new deck — whose
            # cards are all tomorrow's work — banking a day on one review.
            return False
        return await self._due_count(user, now) == 0

    async def _due_count(self, user: User, now: datetime) -> int:
        """How many cards are still due today.

        Reuses the overview's own query rather than a second, cheaper one: the
        predicate behind it is subtle — unstarted cards in a self-paced deck
        are in no queue, and a card added today belongs to tomorrow — and two
        SQL statements that disagree about *that* by a day is a bug nobody
        would find.
        """
        tallies = await self._progress.tally_by_deck_and_box(
            user.id,
            now,
            day_start=day_start_for(user.timezone, now),
            day_end=day_end_for(user.timezone, now),
        )
        return sum(t.due_count for t in tallies if t.started)

    async def _settle_day(self, user_id: UUID, today: date, now: datetime) -> None:
        """Pay the daily goal and bank the streak the first time it is met.

        The once-a-day guarantee is not a check here: the XP award has a
        partial unique index and ``bank_day`` is a single guarded statement, so
        two sessions finishing together cannot both collect.
        """
        user = await self._users.get(user_id)
        if user is None:  # pragma: no cover
            return
        # Cheap and by far the common case on any day already won — it also
        # keeps the due-count query off every grade after the tenth.
        if user.streak_banked_on == today:
            return
        if not await self._goal_met(user, today, now):
            return
        await self._xp.award(
            user_id, XpAction.DAILY_GOAL, occurred_at=now, day=today, once_per_day=True
        )
        await self._users.bank_day(user_id, today)

    async def claim_goal_celebration(self, user_id: UUID) -> CelebrationClaim:
        """Whether *this* client is the one that gets to congratulate them.

        The daily goal is banked once per account per day, but until this
        existed the "you reached your goal" overlay was locked behind a stamp
        in each device's own storage — so a learner who earned it on the phone
        and then opened the PWA was congratulated a second time for the same
        day. There is no device-local answer to that: the fact belongs to the
        account.

        The two refusals are **not** interchangeable, which is why this returns
        three answers and not a boolean:

        - ``TAKEN`` — the day was won and somebody has already been told
          (another device, or an earlier launch of this one). Final: the client
          should stop asking for the rest of the day.
        - ``UNBANKED`` — nothing has been won *yet*. A client counts the day's
          reviews on the device and can cross the goal a round trip before the
          grade that banks it has landed, so this is an ordinary "ask me
          again", not a no. Collapsing it into ``TAKEN`` would lose the
          celebration of the very learner who just earned it — the client
          would stamp its local lock on an answer that only meant "too early".
          A rest day reads as unbanked here too, and is never celebrated, which
          is the existing rule.
        """
        user = await self._users.get(user_id)
        if user is None:  # pragma: no cover
            return CelebrationClaim.UNBANKED
        today = today_for(user.timezone, datetime.now(UTC))
        if user.streak_banked_on != today:
            return CelebrationClaim.UNBANKED
        won = await self._users.claim_goal_celebration(user_id, today)
        return CelebrationClaim.CLAIMED if won else CelebrationClaim.TAKEN

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
        await self._settle_day(user_id, today, now)
        return awarded
