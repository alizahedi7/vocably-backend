"""SQLAlchemy implementation of :class:`DeckUnitRepository`."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.deck_unit import DeckUnit
from app.domain.repositories.deck_unit_repository import DeckUnitRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.deck_unit import DeckUnitModel
from app.infrastructure.db.models.word import WordModel


class SqlAlchemyDeckUnitRepository(DeckUnitRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, unit_id: UUID) -> DeckUnit | None:
        model = await self._session.get(DeckUnitModel, unit_id)
        return mappers.deck_unit_to_entity(model) if model else None

    async def list_for_deck(self, deck_id: UUID) -> list[DeckUnit]:
        stmt = (
            select(DeckUnitModel)
            .where(DeckUnitModel.deck_id == deck_id)
            # id as tie-breaker so two units sharing a position (positions are
            # gapped, not unique) never swap order between requests.
            .order_by(DeckUnitModel.position.asc(), DeckUnitModel.id.asc())
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.deck_unit_to_entity(m) for m in models]

    async def next_position(self, deck_id: UUID) -> int:
        stmt = select(func.max(DeckUnitModel.position)).where(DeckUnitModel.deck_id == deck_id)
        highest = (await self._session.execute(stmt)).scalar_one_or_none()
        return 0 if highest is None else int(highest) + 1

    async def add(self, unit: DeckUnit) -> DeckUnit:
        model = DeckUnitModel(
            id=unit.id, deck_id=unit.deck_id, name=unit.name, position=unit.position
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.deck_unit_to_entity(model)

    async def update(self, unit: DeckUnit) -> DeckUnit:
        model = await self._session.get(DeckUnitModel, unit.id)
        if model is None:
            raise ValueError(f"Deck unit {unit.id} does not exist")
        model.name = unit.name
        model.position = unit.position
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.deck_unit_to_entity(model)

    async def delete(self, unit_id: UUID) -> int:
        loosened = (
            await self._session.execute(
                select(func.count()).select_from(WordModel).where(WordModel.unit_id == unit_id)
            )
        ).scalar_one()
        # The cards themselves are left to ON DELETE SET NULL: the database
        # already expresses "the heading goes, what was under it stays".
        await self._session.execute(delete(DeckUnitModel).where(DeckUnitModel.id == unit_id))
        return int(loosened)
