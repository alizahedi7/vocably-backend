"""SQLAlchemy implementation of :class:`WordProgressRepository`.

Every read here answers "the cards in the decks I belong to, with my state
against them". Because progress rows are created lazily, that is an outer join
whose right side is usually missing — the single most important detail in this
module, and the one that is easiest to break: moving the ``user_id`` predicate
out of the ``ON`` clause turns it into an inner join and silently drops every
word the learner has not studied, which is most of them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, case, delete, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.studied_word import StudiedWord
from app.domain.entities.word_progress import WordProgress
from app.domain.enums import LeitnerBox
from app.domain.repositories.word_progress_repository import (
    DeckBoxTally,
    WordProgressRepository,
)
from app.infrastructure.db import mappers
from app.infrastructure.db.dialects import upsert_insert
from app.infrastructure.db.models.deck_member import DeckMemberModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.models.word_progress import WordProgressModel


class SqlAlchemyWordProgressRepository(WordProgressRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── reads ────────────────────────────────────────────────
    @staticmethod
    def _is_started() -> ColumnElement[bool]:
        """Whether a joined row is in the learner's boxes.

        Self-pacing *is* this expression. A progress row always means started;
        with no row it depends on the membership — an ordinary deck reads
        unstudied (box 1, due now, exactly as it always did), a self-paced one
        reads **not started**, so five hundred saved words stay out of the queue
        until they are asked for.

        Written once and reused by every read below, because a query that
        forgets it either hides a learner's own new words or floods them with a
        stranger's.
        """
        return or_(
            WordProgressModel.word_id.is_not(None),
            DeckMemberModel.self_paced.is_(False),
        )

    @staticmethod
    def _box() -> ColumnElement[int]:
        """Which box a joined row is in.

        A missing progress row reads as box 1 — that is what "never studied"
        means — so the coalesce is the whole of it. Named here for the reason
        :meth:`_is_started` is: it is the definition of "box" for every read,
        and a query that spells it out again is a query that can disagree.
        """
        return func.coalesce(WordProgressModel.box, int(LeitnerBox.NEW))

    @staticmethod
    def _is_due(day_start: datetime, day_end: datetime) -> ColumnElement[bool]:
        """Whether a joined row is waiting for the learner *today*.

        Dueness is a question about the day, not the clock — both branches
        compare against a boundary of the learner's own day, and neither
        against the current instant.

        Without a progress row the card has never been answered and becomes due
        the day *after* it was added, so the question is whether its day has
        arrived. With one, it is due once its scheduled moment falls before the
        end of today; ``day_end`` is tomorrow's first instant, so the
        comparison is strict and a card scheduled for tomorrow stays there.

        Comparing the stored ``due_at`` against *now* is what used to make the
        home screen climb through the day: a card graded at 19:02 came due
        again at 19:02, so the queue grew hour by hour without a midnight ever
        being crossed. A word added this afternoon is tomorrow's work, exactly
        like a word graded ``again`` is.
        """
        return or_(
            and_(WordProgressModel.word_id.is_(None), WordModel.created_at < day_start),
            WordProgressModel.due_at < day_end,
        )

    @staticmethod
    def _day_start(now: datetime, day_start: datetime | None) -> datetime:
        """Falls back to a UTC day boundary when the caller has no timezone.

        Only right for reads that do not turn on due dates (the story
        generator lists a learner's words and cares nothing for when they are
        next seen). Anything the home screen or a session reads must pass the
        learner's own boundary — see ``calendar.day_start_for``.
        """
        if day_start is not None:
            return day_start
        return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    @classmethod
    def _day_bounds(
        cls, now: datetime, day_start: datetime | None, day_end: datetime | None
    ) -> tuple[datetime, datetime]:
        """The learner's ``[start, end)`` day, falling back to a UTC one.

        The end defaults to a day after the start rather than being derived
        again, so the pair cannot disagree about which day it describes. Same
        caveat as :meth:`_day_start`: a caller that turns on due dates must
        pass the learner's own bounds — see ``calendar.day_end_for``.
        """
        start = cls._day_start(now, day_start)
        return start, day_end if day_end is not None else start + timedelta(days=1)

    def _visible(self, user_id: UUID) -> Select[tuple[WordModel, WordProgressModel, bool]]:
        """Cards in the decks this user belongs to, with their own progress.

        The progress half of each row is typed non-optional by SQLAlchemy but
        is ``None`` for every word this learner has not studied — an outer join
        cannot say so in the type. ``_to_studied`` is where that is handled.

        The membership join is what replaced ``words.user_id``: a learner's word
        set is now defined by ``deck_members``, so a card they can no longer see
        stops counting the moment they leave the deck — while their progress row
        survives, ready for a rejoin.
        """
        return (
            select(WordModel, WordProgressModel, self._is_started().label("started"))
            .join(
                DeckMemberModel,
                and_(
                    DeckMemberModel.deck_id == WordModel.deck_id,
                    DeckMemberModel.user_id == user_id,
                ),
            )
            .outerjoin(
                WordProgressModel,
                and_(
                    WordProgressModel.word_id == WordModel.id,
                    # Belongs in the ON clause, not WHERE. In WHERE this becomes
                    # an inner join and every never-studied word disappears.
                    WordProgressModel.user_id == user_id,
                ),
            )
        )

    def _to_studied(
        self,
        word: WordModel,
        progress: WordProgressModel | None,
        started: bool,
        user_id: UUID,
        day_start: datetime,
    ) -> StudiedWord:
        return StudiedWord(
            word=mappers.word_to_entity(word),
            progress=(
                mappers.word_progress_to_entity(progress)
                if progress is not None
                else WordProgress.unstudied(
                    user_id,
                    word.id,
                    word.deck_id,
                    created_at=word.created_at,
                    day_start=day_start,
                )
            ),
            started=bool(started),
        )

    async def get_for_user(
        self,
        word_id: UUID,
        user_id: UUID,
        *,
        day_start: datetime | None = None,
    ) -> StudiedWord | None:
        now = datetime.now(UTC)
        stmt = self._visible(user_id).where(WordModel.id == word_id)
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return self._to_studied(row[0], row[1], row[2], user_id, self._day_start(now, day_start))

    async def list_due(
        self,
        user_id: UUID,
        now: datetime,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        day_start: datetime | None = None,
        day_end: datetime | None = None,
    ) -> list[StudiedWord]:
        # Started first, then due. A never-started card in a self-paced deck
        # has no due date to speak of — it is not in the queue at all.
        boundary, end = self._day_bounds(now, day_start, day_end)
        stmt = self._visible(user_id).where(
            self._is_started(),
            self._is_due(boundary, end),
        )
        if deck_id is not None:
            stmt = stmt.where(WordModel.deck_id == deck_id)
        # COALESCE rather than due_at: a missing row is *due at now* for the
        # predicate, but "now" is a useless sort key — every new word would tie
        # and the queue order would be whatever the plan happened to return.
        # words.created_at reproduces the pre-split order exactly, because
        # inserts set due_at = created_at.
        stmt = stmt.order_by(
            func.coalesce(WordProgressModel.due_at, WordModel.created_at).asc(),
            WordModel.id.asc(),
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.execute(stmt)).all()
        return [self._to_studied(w, p, st, user_id, boundary) for w, p, st in rows]

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
        started_only: bool = False,
        box: LeitnerBox | None = None,
        day_start: datetime | None = None,
    ) -> list[StudiedWord]:
        now = datetime.now(UTC)
        boundary = self._day_start(now, day_start)
        stmt = self._visible(user_id)
        # Asking for a box implies asking for started cards, and the two cannot
        # be separated: a card nobody has started is in no box at all, and its
        # box-1 placeholder would answer `box=1` with a whole self-paced import
        # the learner has never met.
        if started_only or box is not None:
            stmt = stmt.where(self._is_started())
        if box is not None:
            stmt = stmt.where(self._box() == int(box))
        if deck_id is not None:
            stmt = stmt.where(WordModel.deck_id == deck_id)
        stmt = stmt.order_by(WordModel.created_at.desc(), WordModel.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        rows = (await self._session.execute(stmt)).all()
        return [self._to_studied(w, p, st, user_id, boundary) for w, p, st in rows]

    async def tally_by_deck_and_box(
        self,
        user_id: UUID,
        now: datetime,
        *,
        day_start: datetime | None = None,
        day_end: datetime | None = None,
    ) -> list[DeckBoxTally]:
        # A missing progress row reads as box 1 and counts as due. Both are
        # computed in SQL so the box lands in the GROUP BY rather than being
        # patched up in Python over a fetched result set — which is what the
        # home screen used to do, for every due row the learner had.
        box = self._box()
        started = self._is_started()
        due = case(
            (and_(started, self._is_due(*self._day_bounds(now, day_start, day_end))), 1),
            else_=0,
        )
        # `started` joins the grouping rather than filtering the query: the deck
        # screen still has to say "504 words", and the boxes still have to say
        # "12 of them are yours". One query answers both.
        started_flag = case((started, True), else_=False)
        stmt = (
            select(
                WordModel.deck_id,
                box.label("box"),
                started_flag.label("started"),
                func.count().label("word_count"),
                func.coalesce(func.sum(due), 0).label("due_count"),
            )
            .join(
                DeckMemberModel,
                and_(
                    DeckMemberModel.deck_id == WordModel.deck_id,
                    DeckMemberModel.user_id == user_id,
                ),
            )
            .outerjoin(
                WordProgressModel,
                and_(
                    WordProgressModel.word_id == WordModel.id,
                    WordProgressModel.user_id == user_id,
                ),
            )
            # Group by the expression rather than the label: portable across
            # both dialects, where the alias is not.
            .group_by(WordModel.deck_id, box, started_flag)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            DeckBoxTally(
                deck_id=deck_id,
                box=LeitnerBox(int(box_value)),
                started=bool(started_value),
                word_count=int(word_count),
                due_count=int(due_count),
            )
            for deck_id, box_value, started_value, word_count, due_count in rows
        ]

    async def list_unstarted(
        self,
        user_id: UUID,
        deck_id: UUID,
        *,
        unit_id: UUID | None = None,
        word_ids: list[UUID] | None = None,
        limit: int | None = None,
    ) -> list[UUID]:
        """Ids of this deck's cards that are not in the learner's boxes yet.

        In the deck's own order — oldest first — because "add the next ten" has
        to mean the next ten *of the list they are looking at*, not ten at
        random. Unit 1 before Unit 2, and the first ten of a unit before its
        last ten.
        """
        stmt = (
            select(WordModel.id)
            .join(
                DeckMemberModel,
                and_(
                    DeckMemberModel.deck_id == WordModel.deck_id,
                    DeckMemberModel.user_id == user_id,
                ),
            )
            .outerjoin(
                WordProgressModel,
                and_(
                    WordProgressModel.word_id == WordModel.id,
                    WordProgressModel.user_id == user_id,
                ),
            )
            .where(
                WordModel.deck_id == deck_id,
                WordProgressModel.word_id.is_(None),
                DeckMemberModel.self_paced.is_(True),
            )
            .order_by(WordModel.created_at.asc(), WordModel.id.asc())
        )
        if unit_id is not None:
            stmt = stmt.where(WordModel.unit_id == unit_id)
        if word_ids is not None:
            if not word_ids:
                return []
            stmt = stmt.where(WordModel.id.in_(word_ids))
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def start_words(
        self,
        user_id: UUID,
        deck_id: UUID,
        word_ids: list[UUID],
        now: datetime,
        *,
        due_at: datetime,
    ) -> int:
        """Put cards into the learner's boxes: box 1, first review at ``due_at``.

        The same values a missing row reads as — starting a word is not a head
        start, it is joining the queue, and like any other new word it is first
        seen tomorrow. ``DO NOTHING`` on conflict so a double tap adds nothing
        twice and cannot reset a box someone has already moved.
        """
        if not word_ids:
            return 0
        rows = [
            {
                "user_id": user_id,
                "word_id": word_id,
                "deck_id": deck_id,
                "box": int(LeitnerBox.NEW),
                "due_at": due_at,
                "review_count": 0,
                "lapse_count": 0,
                "consecutive_correct": 0,
                "created_at": now,
                "updated_at": now,
            }
            for word_id in word_ids
        ]
        stmt = (
            upsert_insert(self._session)(WordProgressModel)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["user_id", "word_id"])
            .returning(WordProgressModel.word_id)
        )
        return len((await self._session.execute(stmt)).scalars().all())

    async def unstart_words(self, user_id: UUID, word_ids: list[UUID]) -> int:
        """Take cards back out of the boxes — the undo behind "added 20 words".

        Only ever removes rows with no reviews on them. A card that has been
        answered holds real progress, and an undo that could erase it would be a
        different, much worse feature.
        """
        if not word_ids:
            return 0
        stmt = (
            delete(WordProgressModel)
            .where(
                WordProgressModel.user_id == user_id,
                WordProgressModel.word_id.in_(word_ids),
                WordProgressModel.review_count == 0,
            )
            .returning(WordProgressModel.word_id)
        )
        return len((await self._session.execute(stmt)).scalars().all())

    # ── writes ───────────────────────────────────────────────
    async def set_box(self, progress: WordProgress) -> WordProgress:
        """Write the box and due date the learner chose, leaving the rest.

        An upsert like :meth:`record_grade`, because the row may not exist —
        a card in an ordinary deck sits in box 1 from creation without one.
        On insert the values are ``progress``'s own, which for such a card are
        the untouched :meth:`WordProgress.unstudied` defaults; on conflict only
        three columns move, so the review counters and timestamps a real review
        wrote survive a move untouched.

        No counter arithmetic here, and no ``ON CONFLICT`` guard either: two
        moves of one card at the same instant have to resolve to one schedule
        and the most recent answer is the right one to keep, exactly as
        ``box``/``due_at`` already resolve under two concurrent grades.
        """
        model = WordProgressModel
        values = mappers.word_progress_values(progress)
        insert = upsert_insert(self._session)(model).values(**values)
        stmt = insert.on_conflict_do_update(
            index_elements=["user_id", "word_id"],
            set_={
                "deck_id": insert.excluded.deck_id,
                "box": insert.excluded.box,
                "due_at": insert.excluded.due_at,
                "updated_at": insert.excluded.updated_at,
            },
        ).returning(*model.__table__.columns)
        row = (await self._session.execute(stmt)).mappings().one()
        return mappers.word_progress_row_to_entity(row)

    async def record_grade(self, progress: WordProgress, *, is_lapse: bool) -> WordProgress:
        """Persist a graded review, and return the row as it now stands.

        An upsert rather than read-then-insert-or-update, because two concurrent
        first grades of the same word both read "no row" and one INSERT would
        violate the primary key.

        **The counters are incremented in SQL, not overwritten**, and that is
        the other half of the same problem. ``progress`` was computed from a
        read that may already be stale, so writing its counter values back
        would make two concurrent grades land as one — leaving ``review_count``
        disagreeing with ``word_reviews``, which CLAUDE.md says must never
        happen. Adding in the database instead makes them monotonic however
        many requests arrive at once.

        ``box``, ``due_at`` and ``last_grade`` *are* last-writer-wins. That is
        inherent: two grades of one card at the same instant have to resolve to
        one schedule, and the most recent answer is the right one to keep.

        ``first_reviewed_at`` and ``mastered_at`` are coalesced so the earliest
        wins — neither may move once set.
        """
        model = WordProgressModel
        values = mappers.word_progress_values(progress)
        insert = upsert_insert(self._session)(model).values(**values)

        stmt = insert.on_conflict_do_update(
            index_elements=["user_id", "word_id"],
            set_={
                "deck_id": insert.excluded.deck_id,
                "box": insert.excluded.box,
                "due_at": insert.excluded.due_at,
                "last_grade": insert.excluded.last_grade,
                "last_reviewed_at": insert.excluded.last_reviewed_at,
                "updated_at": insert.excluded.updated_at,
                "review_count": model.review_count + 1,
                "lapse_count": model.lapse_count + (1 if is_lapse else 0),
                # A lapse resets the run; anything else extends it.
                "consecutive_correct": (literal(0) if is_lapse else model.consecutive_correct + 1),
                "first_reviewed_at": func.coalesce(
                    model.first_reviewed_at, insert.excluded.first_reviewed_at
                ),
                "mastered_at": func.coalesce(model.mastered_at, insert.excluded.mastered_at),
            },
            # The table's columns, not the entity: RETURNING an ORM class
            # yields a row keyed by entity, and this reads columns.
        ).returning(*model.__table__.columns)

        # RETURNING so the caller answers with the row that actually landed
        # rather than the one it hoped for — under a double-tap those differ.
        row = (await self._session.execute(stmt)).mappings().one()
        return mappers.word_progress_row_to_entity(row)
