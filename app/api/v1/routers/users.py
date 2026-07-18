"""Current-user profile & settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, UserServiceDep
from app.api.v1.schemas.user import CompleteOnboardingIn, UpdateProfileIn, UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def get_me(current_user: CurrentUser) -> UserOut:
    return UserOut.model_validate(current_user)


@router.post("/me/onboarding", response_model=UserOut)
async def complete_onboarding(
    payload: CompleteOnboardingIn,
    current_user: CurrentUser,
    users: UserServiceDep,
) -> UserOut:
    user = await users.complete_onboarding(
        current_user.id,
        name=payload.name,
        age_range=payload.age_range,
        native_language=payload.native_language,
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
        age_range=payload.age_range,
        native_language=payload.native_language,
        app_language=payload.app_language,
        interests=(
            None if payload.interests is None else [str(topic) for topic in payload.interests]
        ),
        daily_goal=payload.daily_goal,
    )
    return UserOut.model_validate(user)
