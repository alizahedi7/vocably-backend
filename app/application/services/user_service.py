"""User profile & settings use cases."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.domain.entities.user import User
from app.domain.enums import AgeRange
from app.domain.repositories.user_repository import UserRepository


class UserService:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def get(self, user_id: UUID) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def complete_onboarding(
        self,
        user_id: UUID,
        *,
        name: str,
        age_range: AgeRange | None,
        native_language: str,
        interests: list[str] | None = None,
        daily_goal: int | None = None,
    ) -> User:
        user = await self.get(user_id)
        user.name = name.strip()
        user.age_range = age_range
        user.native_language = native_language
        if interests is not None:
            user.interests = list(interests)
        if daily_goal is not None:
            user.daily_goal = daily_goal
        user.onboarded = True
        user.updated_at = datetime.now(UTC)
        return await self._users.update(user)

    async def update_profile(
        self,
        user_id: UUID,
        *,
        name: str | None = None,
        age_range: AgeRange | None = None,
        native_language: str | None = None,
        app_language: str | None = None,
        interests: list[str] | None = None,
        daily_goal: int | None = None,
    ) -> User:
        user = await self.get(user_id)
        if name is not None:
            user.name = name.strip()
        if age_range is not None:
            user.age_range = age_range
        if native_language is not None:
            user.native_language = native_language
        if app_language is not None:
            user.app_language = app_language
        if interests is not None:
            user.interests = list(interests)
        if daily_goal is not None:
            user.daily_goal = daily_goal
        user.updated_at = datetime.now(UTC)
        return await self._users.update(user)
