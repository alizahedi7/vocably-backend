"""SQLAlchemy implementation of :class:`DeckDiscoveryRepository`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.entities.deck import Deck
from app.domain.entities.word import Word
from app.domain.repositories.deck_discovery_repository import (
    DeckDiscoveryRepository,
    OutgoingShareView,
    PublicDeckView,
    PublicUnitView,
    SharedDeckView,
)
from app.infrastructure.db import mappers
from app.infrastructure.db.dialects import upsert_insert
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_share import DeckShareModel
from app.infrastructure.db.models.deck_unit import DeckUnitModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel


def _word_counts() -> Select[Any]:
    """Words per deck, as a subquery — never one count query per deck."""
    return select(WordModel.deck_id, func.count().label("n")).group_by(WordModel.deck_id)


class SqlAlchemyDeckDiscoveryRepository(DeckDiscoveryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Explore ──────────────────────────────────────────────
    def _public_select(self, viewer_id: UUID | None) -> Select[Any]:
        counts = _word_counts().subquery()
        # A correlated EXISTS rather than a join: one boolean per row, and no
        # risk of a learner who somehow holds two copies duplicating the deck
        # in Explore.
        copy = aliased(DeckModel)
        saved = (
            select(1)
            .where(
                copy.user_id == viewer_id,
                copy.copied_from_deck_id == DeckModel.id,
            )
            .exists()
            if viewer_id is not None
            else None
        )
        return (
            select(
                DeckModel,
                UserModel.name,
                UserModel.username,
                func.coalesce(counts.c.n, 0),
                # Literal false when nobody in particular is browsing, so every
                # caller reads the same five columns.
                saved if saved is not None else sa_false(),
            )
            # An outer join on the author: an official deck may have no author
            # to show, and a published deck must not vanish because of it.
            .outerjoin(UserModel, UserModel.id == DeckModel.user_id)
            .outerjoin(counts, counts.c.deck_id == DeckModel.id)
            .where(DeckModel.is_public.is_(True))
        )

    def _to_public(
        self,
        deck: DeckModel,
        name: str | None,
        username: str | None,
        words: int,
        saved: bool,
    ) -> PublicDeckView:
        return PublicDeckView(
            deck=mappers.deck_to_entity(deck),
            word_count=int(words),
            # Official decks are Vocably's, so they credit nobody. For a
            # learner's deck the handle is published deliberately — this is the
            # only place one is, which is why a private deck's owner must never
            # reach this serialiser.
            author_name="" if deck.is_official else (name or ""),
            author_username="" if deck.is_official else (username or ""),
            is_official=deck.is_official,
            category=deck.category,
            description=deck.description,
            description_fa=deck.description_fa,
            saves=deck.save_count,
            saved=bool(saved),
        )

    async def list_public(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        limit: int,
        offset: int,
        viewer_id: UUID | None = None,
    ) -> list[PublicDeckView]:
        stmt = self._public_select(viewer_id)
        if category:
            stmt = stmt.where(DeckModel.category == category)
        if query:
            # Matches the client's local filter: name or author handle.
            like = f"%{query.strip().lower()}%"
            stmt = stmt.where(
                func.lower(DeckModel.name).like(like) | func.lower(UserModel.username).like(like)
            )
        stmt = (
            # Official first, then most-saved: a new learner's Explore should
            # open on the decks Vocably stands behind.
            stmt.order_by(
                DeckModel.is_official.desc(),
                DeckModel.save_count.desc(),
                DeckModel.published_at.desc().nullslast(),
                DeckModel.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        return [self._to_public(*row) for row in rows]

    async def get_public(
        self, deck_id: UUID, *, viewer_id: UUID | None = None
    ) -> PublicDeckView | None:
        stmt = self._public_select(viewer_id).where(DeckModel.id == deck_id)
        row = (await self._session.execute(stmt)).first()
        return None if row is None else self._to_public(*row)

    async def list_public_units(self, deck_id: UUID) -> list[PublicUnitView]:
        counts = (
            select(WordModel.unit_id, func.count().label("n"))
            .where(WordModel.deck_id == deck_id)
            .group_by(WordModel.unit_id)
            .subquery()
        )
        stmt = (
            select(DeckUnitModel, func.coalesce(counts.c.n, 0))
            # Outer: an empty section is still a section, and a coursebook
            # whose last lesson has no cards yet must still list it.
            .outerjoin(counts, counts.c.unit_id == DeckUnitModel.id)
            .where(DeckUnitModel.deck_id == deck_id)
            .order_by(DeckUnitModel.position, DeckUnitModel.id)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            PublicUnitView(id=unit.id, name=unit.name, position=unit.position, word_count=int(n))
            for unit, n in rows
        ]

    async def list_public_words(
        self,
        deck_id: UUID,
        *,
        unit_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> list[Word]:
        stmt = select(WordModel).where(WordModel.deck_id == deck_id)
        if unit_id is not None:
            stmt = stmt.where(WordModel.unit_id == unit_id)
        stmt = (
            # The same order a copy is made in, and for the same reason: a
            # coursebook is a sequence, so Lesson 1's first word is first.
            stmt.order_by(WordModel.created_at.asc(), WordModel.id.asc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [mappers.word_to_entity(row) for row in rows]

    async def copy_deck_to(self, deck_id: UUID, user_id: UUID) -> Deck:
        source = await self._session.get(DeckModel, deck_id)
        if source is None:  # pragma: no cover — caller checked visibility first
            raise ValueError(f"Deck {deck_id} does not exist")

        copy = DeckModel(
            id=uuid.uuid4(),
            user_id=user_id,
            name=source.name,
            hue=source.hue,
            # Carried like the hue: how a deck looks is a property of the deck,
            # not of whose list it sits in. A learner who saves 504 keeps
            # recognising it among their own decks.
            icon=source.icon,
            # The copy is the learner's own private deck. Publishing it again
            # is a separate, deliberate act — copies must not inherit a place
            # in Explore, or one popular deck would spawn a hundred listings.
            is_public=False,
            is_official=False,
            category=source.category,
            description=source.description,
            description_fa=source.description_fa,
            # Provenance, so Explore can say "Saved" on the deck this came
            # from. Nothing else reads it: the copy is independent.
            copied_from_deck_id=deck_id,
        )
        self._session.add(copy)
        await self._session.flush()

        # Units first, so words can point at the copies rather than the
        # originals — a card in another deck's unit would be invisible.
        unit_map: dict[UUID, UUID] = {}
        units = (
            (
                await self._session.execute(
                    select(DeckUnitModel)
                    .where(DeckUnitModel.deck_id == deck_id)
                    .order_by(DeckUnitModel.position)
                )
            )
            .scalars()
            .all()
        )
        for unit in units:
            new_id = uuid.uuid4()
            unit_map[unit.id] = new_id
            self._session.add(
                DeckUnitModel(id=new_id, deck_id=copy.id, name=unit.name, position=unit.position)
            )

        # In the source deck's own order, and *keeping* each card's created_at.
        # A course deck is a sequence — Unit 1's first word is first for a
        # reason — and stamping five hundred copies with one timestamp threw
        # that away, leaving the list in uuid order. It also matters now that
        # "start the next ten" means the next ten of the book.
        words = (
            (
                await self._session.execute(
                    select(WordModel)
                    .where(WordModel.deck_id == deck_id)
                    .order_by(WordModel.created_at.asc(), WordModel.id.asc())
                )
            )
            .scalars()
            .all()
        )
        for word in words:
            self._session.add(
                WordModel(
                    id=uuid.uuid4(),
                    deck_id=copy.id,
                    # The copier is the author of their copy. Crediting the
                    # original would let deleting that account touch rows in a
                    # deck they have nothing to do with.
                    created_by_user_id=user_id,
                    unit_id=unit_map.get(word.unit_id) if word.unit_id else None,
                    term=word.term,
                    meaning=word.meaning,
                    definition=word.definition,
                    example=word.example,
                    sense_label=word.sense_label,
                    # Copied, unlike authorship: how a word is pronounced is a
                    # property of the word, not of whose deck it sits in, and
                    # re-fetching it for every copy would call the dictionary
                    # five hundred times for an answer already on the row.
                    phonetic=word.phonetic,
                    created_at=word.created_at,
                )
            )
        # No progress rows — and for a copy that now means *not started*, not
        # "new and due". The membership added alongside this copy is self-paced,
        # so the learner takes these words on at their own rate instead of
        # finding all five hundred in tomorrow's review queue.
        await self._session.flush()
        await self._session.refresh(copy)
        return mappers.deck_to_entity(copy)

    async def increment_saves(self, deck_id: UUID) -> None:
        await self._session.execute(
            update(DeckModel)
            .where(DeckModel.id == deck_id)
            .values(save_count=DeckModel.save_count + 1)
        )

    async def set_listing_metadata(
        self,
        deck_id: UUID,
        *,
        category: str,
        description: str,
        description_fa: str,
    ) -> None:
        await self._session.execute(
            update(DeckModel)
            .where(DeckModel.id == deck_id)
            .values(
                category=category,
                description=description,
                description_fa=description_fa,
            )
        )

    async def set_published(
        self,
        deck_id: UUID,
        *,
        is_public: bool,
        is_official: bool,
        category: str | None,
        description: str | None,
        description_fa: str | None,
        published_at: datetime | None,
    ) -> None:
        values: dict[str, object] = {
            "is_public": is_public,
            "is_official": is_official,
            "published_at": published_at,
        }
        # Omitted fields keep what the deck already has, so re-publishing to
        # fix a typo in one of them cannot blank the others.
        if category is not None:
            values["category"] = category
        if description is not None:
            values["description"] = description
        if description_fa is not None:
            values["description_fa"] = description_fa
        await self._session.execute(
            update(DeckModel).where(DeckModel.id == deck_id).values(**values)
        )

    # ── person-to-person offers ──────────────────────────────
    def _share_select(self) -> Select[Any]:
        counts = _word_counts().subquery()
        sender = aliased(UserModel)
        return (
            select(
                DeckShareModel,
                DeckModel,
                sender.name,
                sender.username,
                func.coalesce(counts.c.n, 0),
            )
            .join(DeckModel, DeckModel.id == DeckShareModel.deck_id)
            .outerjoin(sender, sender.id == DeckShareModel.from_user_id)
            .outerjoin(counts, counts.c.deck_id == DeckModel.id)
        )

    def _to_share(
        self,
        share: DeckShareModel,
        deck: DeckModel,
        name: str | None,
        username: str | None,
        words: int,
    ) -> SharedDeckView:
        return SharedDeckView(
            share_id=share.id,
            deck=mappers.deck_to_entity(deck),
            to_user_id=share.to_user_id,
            role=share.role,
            word_count=int(words),
            from_name=name or "",
            from_username=username or "",
            shared_at=share.shared_at,
            accepted=share.accepted,
        )

    async def list_shares_for(self, user_id: UUID) -> list[SharedDeckView]:
        stmt = (
            self._share_select()
            .where(DeckShareModel.to_user_id == user_id)
            .order_by(DeckShareModel.shared_at.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [self._to_share(*row) for row in rows]

    async def list_pending_shares_of(self, deck_id: UUID) -> list[OutgoingShareView]:
        recipient = aliased(UserModel)
        stmt = (
            select(
                recipient.username,
                recipient.name,
                DeckShareModel.role,
                DeckShareModel.shared_at,
            )
            .join(recipient, recipient.id == DeckShareModel.to_user_id)
            .where(
                DeckShareModel.deck_id == deck_id,
                DeckShareModel.accepted.is_(False),
            )
            .order_by(DeckShareModel.shared_at)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            OutgoingShareView(
                to_username=username or "",
                to_name=name or "",
                role=role,
                shared_at=shared_at,
            )
            for username, name, role, shared_at in rows
        ]

    async def get_share(self, share_id: UUID) -> SharedDeckView | None:
        stmt = self._share_select().where(DeckShareModel.id == share_id)
        row = (await self._session.execute(stmt)).first()
        return None if row is None else self._to_share(*row)

    async def offer(
        self,
        deck_id: UUID,
        *,
        from_user_id: UUID,
        to_user_id: UUID,
        role: str,
        shared_at: datetime,
    ) -> None:
        # Re-sharing refreshes the existing offer rather than stacking a second
        # one in the recipient's list — and never un-accepts one they took.
        stmt = (
            upsert_insert(self._session)(DeckShareModel)
            .values(
                id=uuid.uuid4(),
                deck_id=deck_id,
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                role=role,
                shared_at=shared_at,
                accepted=False,
            )
            .on_conflict_do_update(
                index_elements=["deck_id", "to_user_id"],
                set_={"shared_at": shared_at, "from_user_id": from_user_id, "role": role},
            )
        )
        await self._session.execute(stmt)

    async def mark_accepted(self, share_id: UUID) -> None:
        await self._session.execute(
            update(DeckShareModel)
            .where(DeckShareModel.id == share_id)
            .values(accepted=True, updated_at=datetime.now(UTC))
        )

    async def withdraw(self, share_id: UUID) -> None:
        await self._session.execute(delete(DeckShareModel).where(DeckShareModel.id == share_id))
