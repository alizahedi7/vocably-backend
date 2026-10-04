"""SQLAlchemy implementation of :class:`UserRepository`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, and_, case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, IdentifierInUseError
from app.domain.entities.user import User
from app.domain.repositories.user_repository import UserRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.user import UserModel


class SqlAlchemyUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID) -> User | None:
        model = await self._session.get(UserModel, user_id)
        return mappers.user_to_entity(model) if model else None

    async def get_by_phone(self, phone: str) -> User | None:
        stmt = select(UserModel).where(UserModel.phone == phone)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return mappers.user_to_entity(model) if model else None

    async def get_by_google_sub(self, google_sub: str) -> User | None:
        stmt = select(UserModel).where(UserModel.google_sub == google_sub)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return mappers.user_to_entity(model) if model else None

    async def get_by_verified_email(self, email: str) -> User | None:
        stmt = select(UserModel).where(
            UserModel.email == email,
            UserModel.is_email_verified.is_(True),
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return mappers.user_to_entity(model) if model else None

    async def reload(self, user_id: UUID) -> User | None:
        # ``populate_existing`` because ``get`` alone answers from the identity
        # map — the very copy the caller is asking to see past.
        model = await self._session.get(UserModel, user_id, populate_existing=True)
        return mappers.user_to_entity(model) if model else None

    async def link_phone(self, user_id: UUID, phone: str) -> User:
        return await self._link(user_id, {"phone": phone})

    async def link_email(self, user_id: UUID, email: str) -> User:
        return await self._link(user_id, {"email": email, "is_email_verified": True})

    async def _link(self, user_id: UUID, values: dict[str, Any]) -> User:
        try:
            await self._session.execute(
                update(UserModel).where(UserModel.id == user_id).values(**values)
            )
        except IntegrityError as exc:
            # The statement sets one identifier and nothing else, so the only
            # constraint it can break is that identifier's unique index — no
            # matching on the message, which here would contain a string the
            # caller chose.
            raise IdentifierInUseError() from exc
        user = await self.reload(user_id)
        if user is None:
            raise ValueError(f"User {user_id} does not exist")
        return user

    async def unlink_phone(self, user_id: UUID) -> User | None:
        stmt = (
            update(UserModel)
            # The lockout rule and its race guard in one predicate, as in
            # ``bank_day``: a request removing the email at the same moment
            # clears ``google_sub`` first, and this then matches nothing.
            .where(UserModel.id == user_id, UserModel.google_sub.is_not(None))
            .values(phone=None)
        )
        return await self._unlinked(user_id, stmt)

    async def unlink_email(self, user_id: UUID) -> User | None:
        stmt = (
            update(UserModel)
            .where(UserModel.id == user_id, UserModel.phone.is_not(None))
            .values(email=None, is_email_verified=False, google_sub=None)
        )
        return await self._unlinked(user_id, stmt)

    async def _unlinked(self, user_id: UUID, stmt: Any) -> User | None:
        result = await self._session.execute(stmt)
        if not cast("CursorResult[Any]", result).rowcount:
            return None
        return await self.reload(user_id)

    async def list_by_ids(self, user_ids: Sequence[UUID]) -> dict[UUID, User]:
        if not user_ids:
            return {}
        stmt = select(UserModel).where(UserModel.id.in_(list(user_ids)))
        models = (await self._session.execute(stmt)).scalars().all()
        return {m.id: mappers.user_to_entity(m) for m in models}

    async def delete(self, user_id: UUID) -> None:
        await self._session.execute(delete(UserModel).where(UserModel.id == user_id))

    async def get_by_username(self, username: str) -> User | None:
        stmt = select(UserModel).where(UserModel.username == username)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return mappers.user_to_entity(model) if model else None

    async def search_by_username(
        self, prefix: str, *, exclude_user_id: UUID, limit: int
    ) -> list[User]:
        # ``startswith`` with autoescape, so a handle containing ``_`` — which
        # is legal, and a LIKE wildcard — matches itself rather than any
        # character. The unique index on ``username`` serves the prefix range.
        stmt = (
            select(UserModel)
            .where(
                UserModel.username.is_not(None),
                UserModel.username.startswith(prefix, autoescape=True),
                UserModel.id != exclude_user_id,
            )
            .order_by(func.length(UserModel.username), UserModel.username)
            .limit(limit)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [mappers.user_to_entity(m) for m in models]

    async def username_taken(self, username: str) -> bool:
        stmt = select(UserModel.id).where(UserModel.username == username).limit(1)
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def add(self, user: User) -> User:
        model = UserModel(id=user.id)
        mappers.apply_user(user, model)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.user_to_entity(model)

    async def update(self, user: User) -> User:
        model = await self._session.get(UserModel, user.id)
        if model is None:
            raise ValueError(f"User {user.id} does not exist")
        mappers.apply_user(user, model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # The unique index is the real arbiter of a handle, not the
            # availability check before it: two people can pass that check in
            # the same instant and only one row can win. Translating here keeps
            # the loser on a 409 with copy they can read, rather than a 500.
            if _is_username_conflict(exc):
                raise AlreadyExistsError("That username is already taken.") from exc
            raise
        await self._session.refresh(model)
        return mappers.user_to_entity(model)

    async def bank_day(self, user_id: UUID, today: date) -> bool:
        yesterday = today - timedelta(days=1)
        stmt = (
            update(UserModel)
            .where(
                UserModel.id == user_id,
                # The whole once-a-day guarantee, and the race guard, in one
                # predicate. Two sessions finishing together both run this
                # statement; the second matches nothing.
                or_(
                    UserModel.streak_banked_on.is_(None),
                    UserModel.streak_banked_on < today,
                ),
            )
            .values(
                # Derived from the stored value in SQL rather than from one
                # read a moment ago — the rule in
                # app.domain.services.streak.advanced, expressed where it
                # cannot lose a concurrent write.
                streak=case(
                    (
                        and_(
                            UserModel.streak_last_day.is_not(None),
                            UserModel.streak_last_day >= yesterday,
                        ),
                        UserModel.streak + 1,
                    ),
                    else_=1,
                ),
                streak_last_day=today,
                streak_banked_on=today,
            )
        )
        result = await self._session.execute(stmt)
        # `execute` is typed as returning a Result; an UPDATE always yields a
        # CursorResult, which is where rowcount lives.
        return bool(cast("CursorResult[Any]", result).rowcount)

    async def claim_goal_celebration(self, user_id: UUID, today: date) -> bool:
        stmt = (
            update(UserModel)
            .where(
                UserModel.id == user_id,
                # The whole once-a-day-per-account guarantee, and the race
                # guard, in one predicate — the phone and the PWA both
                # refreshing at the same instant run this statement, and the
                # second matches nothing.
                or_(
                    UserModel.goal_celebrated_on.is_(None),
                    UserModel.goal_celebrated_on < today,
                ),
            )
            .values(goal_celebrated_on=today)
        )
        result = await self._session.execute(stmt)
        return bool(cast("CursorResult[Any]", result).rowcount)

    async def settle_streak(self, user_id: UUID, *, days: int, last_day: date | None) -> None:
        stmt = (
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(streak=days, streak_last_day=last_day)
        )
        await self._session.execute(stmt)


def _is_username_conflict(exc: IntegrityError) -> bool:
    """Whether this violation is the handle's unique index.

    Matched on the text because the constraint is named differently by each
    dialect, and a user row has several unique columns — a phone or google_sub
    conflict must never be reported to someone as a taken handle.
    """
    return "username" in str(exc.orig).lower()
