"""Current-user profile & settings endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    CurrentUser,
    UserServiceDep,
    enforce_user_search_limit,
    enforce_username_check_limit,
)
from app.api.v1.schemas.user import (
    CompleteOnboardingIn,
    PeopleOut,
    PersonOut,
    UpdateProfileIn,
    UsernameAvailableOut,
    UserOut,
)
from app.domain.services.usernames import USERNAME_MAX_LENGTH

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/username-available",
    response_model=UsernameAvailableOut,
    dependencies=[Depends(enforce_username_check_limit)],
)
async def username_available(
    users: UserServiceDep,
    username: Annotated[str, Query(max_length=USERNAME_MAX_LENGTH, description="Handle to check")],
) -> UsernameAvailableOut:
    """Whether a handle can be claimed.

    Called as the learner types and again before the flow advances, since a
    handle can be claimed between the check and the commit — so this is
    advisory, and the unique index is what actually decides.

    Malformed and reserved handles answer ``false`` rather than erroring: the
    client treats a *failure* as "unknown" and lets the user through, which is
    deliberate, but must not be reachable by typing.
    """
    return UsernameAvailableOut(available=await users.is_username_available(username))


@router.get(
    "/search",
    response_model=PeopleOut,
    dependencies=[Depends(enforce_user_search_limit)],
)
async def search_people(
    current_user: CurrentUser,
    users: UserServiceDep,
    q: Annotated[
        str,
        Query(max_length=USERNAME_MAX_LENGTH + 1, description="Start of a handle"),
    ],
) -> PeopleOut:
    """Find someone by the start of their handle, so a sharer can pick a name
    instead of spelling one exactly.

    Handles only — never the display name. A handle is the one string a learner
    picks *so that* other people can address them; a name is not, and making it
    searchable would publish something nobody opted into.

    A prefix shorter than two characters, or one that could not begin a handle,
    answers an empty list rather than an error: the caller is typing, and half a
    keystroke has no answer worth an exception.
    """
    found = await users.search_by_handle(q, searcher_id=current_user.id)
    return PeopleOut(people=[PersonOut.from_user(u) for u in found])


@router.get("/me", response_model=UserOut)
async def get_me(current_user: CurrentUser, users: UserServiceDep) -> UserOut:
    return UserOut.model_validate(await users.settled(current_user))


@router.post("/me/onboarding", response_model=UserOut)
async def complete_onboarding(
    payload: CompleteOnboardingIn,
    current_user: CurrentUser,
    users: UserServiceDep,
) -> UserOut:
    user = await users.complete_onboarding(
        current_user.id,
        name=payload.name,
        username=payload.username,
        age_range=payload.age_range,
        native_language=payload.native_language,
        target_language=payload.target_language,
        proficiency=payload.proficiency,
        study_time=payload.study_time,
        timezone=payload.timezone,
        interests=[str(topic) for topic in payload.interests],
        daily_goal=payload.daily_goal,
    )
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UpdateProfileIn,
    current_user: CurrentUser,
    users: UserServiceDep,
) -> UserOut:
    user = await users.update_profile(
        current_user.id,
        name=payload.name,
        username=payload.username,
        age_range=payload.age_range,
        native_language=payload.native_language,
        app_language=payload.app_language,
        target_language=payload.target_language,
        proficiency=payload.proficiency,
        study_time=payload.study_time,
        timezone=payload.timezone,
        interests=(
            None if payload.interests is None else [str(topic) for topic in payload.interests]
        ),
        daily_goal=payload.daily_goal,
    )
    return UserOut.model_validate(user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(current_user: CurrentUser, users: UserServiceDep) -> None:
    """Erase the account and everything keyed to it.

    Refused with 409 while the caller still owns a deck other people are in:
    deleting them would take a class's vocabulary with it. Cards they wrote in
    someone else's deck stay, uncredited — they belong to that deck.
    """
    await users.delete_account(current_user.id)
