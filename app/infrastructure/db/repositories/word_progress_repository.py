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

from sqlalchemy import Select, and_, case, func, or_, select
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

#: Everything a grade rewrites. ``created_at`` is deliberately absent — it
#: records when the learner first met the word and must survive later grades.
_ON_GRADE = (
    "deck_id",
    "box",
    "due_at",
    "review_count",
    "last_reviewed_at",
    "lapse_count",
    "consecutive_correct",
    "first_reviewed_at",
    "mastered_at",
    "last_grade",
    "updated_at",
)


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
    async def upsert(self, progress: WordProgress) -> None:
        # An upsert rather than read-then-insert-or-update, and that is a
        # correctness choice: two concurrent first grades of the same word both
        # read "no row", and one INSERT would then violate the primary key and
        # lose a learner's review to a 500.
        stmt = upsert_insert(self._session)(WordProgressModel).values(
            **mappers.word_progress_values(progress)
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["user_id", "word_id"],
                set_={column: stmt.excluded[column] for column in _ON_GRADE},
            )
        )
