"""User profile & settings use cases."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.exceptions import (
    AlreadyExistsError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.domain.entities.user import User
from app.domain.enums import AgeRange
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.deck_repository import DeckRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.services import usernames
from app.domain.services.calendar import is_valid_timezone


class UserService:
    def __init__(
        self,
        users: UserRepository,
        decks: DeckRepository,
        members: DeckMemberRepository,
    ) -> None:
        self._users = users
        self._decks = decks
        self._members = members

    async def get(self, user_id: UUID) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def is_username_available(self, username: str) -> bool:
        """Whether a handle can be claimed right now.

        Malformed and reserved handles answer ``False`` rather than raising: the
        client treats a *failure* as "unknown" and lets the user through, which
        is deliberate — being unable to ask is not a rejection — but must not be
        reachable by typing.
        """
        candidate = usernames.normalize(username)
        if not usernames.is_valid_username(candidate):
            return False
        return not await self._users.username_taken(candidate)

    async def _claim_username(self, user: User, raw: str) -> None:
        """Validate and assign a handle, or raise the copy the user will read."""
        candidate = usernames.normalize(raw)
        if candidate == user.username:
            return
        if not usernames.is_valid_username(candidate):
            raise ValidationError(
                "Handles are 3–20 characters, start with a letter, "
                "and use only letters, numbers and underscores."
            )
        if await self._users.username_taken(candidate):
            # The unique index is the real guarantee — this check only exists to
            # turn the race into good copy rather than a 500. The repository
            # raising on the index is still the backstop.
            raise AlreadyExistsError("That handle is already taken.")
        user.username = candidate

    async def complete_onboarding(
        self,
        user_id: UUID,
        *,
        name: str,
        age_range: AgeRange | None,
        native_language: str,
        username: str | None = None,
        target_language: str | None = None,
        proficiency: str | None = None,
        study_time: str | None = None,
        timezone: str | None = None,
        interests: list[str] | None = None,
        daily_goal: int | None = None,
    ) -> User:
        user = await self.get(user_id)
        user.name = name.strip()
        user.age_range = age_range
        user.native_language = native_language
        if username is not None:
            await self._claim_username(user, username)
        if target_language is not None:
            user.target_language = target_language
        if proficiency is not None:
            user.proficiency = proficiency
        if study_time is not None:
            user.study_time = study_time
        if timezone is not None:
            user.timezone = _validated_timezone(timezone)
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
        username: str | None = None,
        target_language: str | None = None,
        proficiency: str | None = None,
        study_time: str | None = None,
        timezone: str | None = None,
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
        if username is not None:
            await self._claim_username(user, username)
        if target_language is not None:
            user.target_language = target_language
        if proficiency is not None:
            user.proficiency = proficiency
        if study_time is not None:
            user.study_time = study_time
        if timezone is not None:
            user.timezone = _validated_timezone(timezone)
        if interests is not None:
            user.interests = list(interests)
        if daily_goal is not None:
            user.daily_goal = daily_goal
        user.updated_at = datetime.now(UTC)
        return await self._users.update(user)

    async def delete_account(self, user_id: UUID) -> None:
        """Erase an account, refusing while it still owns a shared deck.

        Deleting the owner of a class deck would take thirty students'
        vocabulary with it, so that case is blocked rather than being quietly
        destructive; handing the deck over or deleting it is a decision its
        owner has to make deliberately. Everything else goes: their progress,
        their review history, their memberships, their daily activity, all by
        cascade from ``users``.

        The **order matters**. The user's own decks are deleted first, because
        the account delete also fires ``SET NULL`` on cards they wrote — and an
        update to a card whose deck is being cascaded away in the same
        statement fails on the deck foreign key. Removing the decks up front
        leaves only cards in *other people's* decks to be uncredited, which is
        exactly the set that should survive.
        """
        shared = await self._members.shared_deck_names_owned_by(user_id)
        if shared:
            names = ", ".join(f"\u201c{name}\u201d" for name in shared)
            raise ConflictError(
                f"Hand over or delete {names} first — other people are still using it."
                if len(shared) == 1
                else f"Hand over or delete {names} first — other people are still using them."
            )
        await self._decks.delete_many(await self._members.owned_deck_ids(user_id))
        await self._users.delete(user_id)


def _validated_timezone(timezone: str) -> str:
    """Reject an unresolvable IANA name at the write.

    Reads fall back to UTC rather than raising (see ``calendar.zone_for``); the
    write is where a bad value should be refused, so a wrong day boundary is
    never persisted silently.
    """
    if not is_valid_timezone(timezone):
        raise ValidationError(f"{timezone!r} is not a known timezone.")
    return timezone
