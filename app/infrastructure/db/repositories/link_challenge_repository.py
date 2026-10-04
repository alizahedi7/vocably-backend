"""SQLAlchemy implementation of :class:`LinkChallengeRepository`."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.link_challenge import LinkChallenge
from app.domain.enums import IdentifierType
from app.domain.repositories.link_challenge_repository import LinkChallengeRepository
from app.infrastructure.db.dialects import upsert_insert
from app.infrastructure.db.models.link_challenge import LinkChallengeModel


class SqlAlchemyLinkChallengeRepository(LinkChallengeRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def put(self, challenge: LinkChallenge) -> None:
        replaced = {
            "target": challenge.target,
            "code_hash": challenge.code_hash,
            "attempts_left": challenge.attempts_left,
            "expires_at": challenge.expires_at,
            "created_at": challenge.created_at,
        }
        # One statement, because delete-then-insert races: two taps on "send
        # code" both delete nothing and the second insert violates the key.
        stmt = (
            upsert_insert(self._session)(LinkChallengeModel)
            .values(user_id=challenge.user_id, kind=challenge.kind.value, **replaced)
            .on_conflict_do_update(index_elements=["user_id", "kind"], set_=replaced)
        )
        await self._session.execute(stmt)

    async def get(self, user_id: UUID, kind: IdentifierType) -> LinkChallenge | None:
        # Columns rather than the ORM entity: every write here is a Core
        # statement, and an entity in the identity map would go on answering
        # with the attempts it had before the last one was spent.
        stmt = select(*LinkChallengeModel.__table__.columns).where(*_key(user_id, kind))
        row = (await self._session.execute(stmt)).mappings().one_or_none()
        if row is None:
            return None
        return LinkChallenge(
            user_id=row["user_id"],
            kind=IdentifierType(row["kind"]),
            target=row["target"],
            code_hash=row["code_hash"],
            attempts_left=row["attempts_left"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )

    async def spend_attempt(self, user_id: UUID, kind: IdentifierType) -> int:
        stmt = (
            update(LinkChallengeModel)
            .where(*_key(user_id, kind), LinkChallengeModel.attempts_left > 0)
            .values(attempts_left=LinkChallengeModel.attempts_left - 1)
            .returning(LinkChallengeModel.attempts_left)
        )
        left = (await self._session.execute(stmt)).scalar_one_or_none()
        # No row matched: already spent by a request that got here first.
        return left or 0

    async def consume(self, user_id: UUID, kind: IdentifierType, code_hash: str) -> bool:
        stmt = delete(LinkChallengeModel).where(
            *_key(user_id, kind),
            # Pinned to the code that was checked, so a challenge replaced
            # between the read and this delete is not consumed in its place.
            LinkChallengeModel.code_hash == code_hash,
        )
        result = await self._session.execute(stmt)
        return bool(cast("CursorResult[Any]", result).rowcount)


def _key(user_id: UUID, kind: IdentifierType) -> tuple[Any, Any]:
    return (LinkChallengeModel.user_id == user_id, LinkChallengeModel.kind == kind.value)
