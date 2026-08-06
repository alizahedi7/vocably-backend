"""SQLAlchemy implementation of :class:`WordProgressRepository`.

Every read here answers "the cards in the decks I belong to, with my state
against them". Because progress rows are created lazily, that is an outer join
whose right side is usually missing — the single most important detail in this
module, and the one that is easiest to break: moving the ``user_id`` predicate
out of the ``ON`` clause turns it into an inner join and silently drops every
word the learner has not studied, which is most of them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.studied_word import StudiedWord
from app.domain.entities.word_progress import WordProgress
from app.domain.enums import LeitnerBox, ReviewGrade
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
    def _visible(self, user_id: UUID) -> Select[tuple[WordModel, WordProgressModel]]:
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
            select(WordModel, WordProgressModel)
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
        user_id: UUID,
        now: datetime,
    ) -> StudiedWord:
        return StudiedWord(
            word=mappers.word_to_entity(word),
            progress=(
                mappers.word_progress_to_entity(progress)
                if progress is not None
                else WordProgress.unstudied(user_id, word.id, word.deck_id, now)
            ),
        )

    async def get_for_user(self, word_id: UUID, user_id: UUID) -> StudiedWord | None:
        now = datetime.now(UTC)
        stmt = self._visible(user_id).where(WordModel.id == word_id)
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return self._to_studied(row[0], row[1], user_id, now)

    async def list_due(
        self,
        user_id: UUID,
        now: datetime,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[StudiedWord]:
        stmt = self._visible(user_id).where(
            or_(WordProgressModel.word_id.is_(None), WordProgressModel.due_at <= now)
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
        return [self._to_studied(w, p, user_id, now) for w, p in rows]

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        deck_id: UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[StudiedWord]:
        now = datetime.now(UTC)
        stmt = self._visible(user_id)
        if deck_id is not None:
            stmt = stmt.where(WordModel.deck_id == deck_id)
        stmt = stmt.order_by(WordModel.created_at.desc(), WordModel.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        rows = (await self._session.execute(stmt)).all()
        return [self._to_studied(w, p, user_id, now) for w, p in rows]

    async def tally_by_deck_and_box(self, user_id: UUID, now: datetime) -> list[DeckBoxTally]:
        # A missing progress row reads as box 1 and counts as due. Both are
        # computed in SQL so the box lands in the GROUP BY rather than being
        # patched up in Python over a fetched result set — which is what the
        # home screen used to do, for every due row the learner had.
        box = func.coalesce(WordProgressModel.box, int(LeitnerBox.NEW))
        due = case(
            (
                or_(WordProgressModel.word_id.is_(None), WordProgressModel.due_at <= now),
                1,
            ),
            else_=0,
        )
        stmt = (
            select(
                WordModel.deck_id,
                box.label("box"),
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
            .group_by(WordModel.deck_id, box)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            DeckBoxTally(
                deck_id=deck_id,
                box=LeitnerBox(int(box_value)),
                word_count=int(word_count),
                due_count=int(due_count),
            )
            for deck_id, box_value, word_count, due_count in rows
        ]

    # ── writes ───────────────────────────────────────────────
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
        return WordProgress(
            user_id=row["user_id"],
            word_id=row["word_id"],
            deck_id=row["deck_id"],
            box=LeitnerBox(row["box"]),
            due_at=row["due_at"],
            review_count=row["review_count"],
            last_reviewed_at=row["last_reviewed_at"],
            lapse_count=row["lapse_count"],
            consecutive_correct=row["consecutive_correct"],
            first_reviewed_at=row["first_reviewed_at"],
            mastered_at=row["mastered_at"],
            last_grade=(
                ReviewGrade.from_ordinal(row["last_grade"])
                if row["last_grade"] is not None
                else None
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
