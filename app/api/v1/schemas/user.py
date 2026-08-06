"""User request/response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import AgeRange, AuthMethod, InterestTopic
from app.domain.services.usernames import USERNAME_MAX_LENGTH

#: Allowed daily new-word goals (mirrors DAILY_GOAL_CHOICES in the domain).
DailyGoal = Literal[5, 10, 15, 20]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    auth_method: AuthMethod
    phone: str | None
    email: str | None
    name: str
    username: str | None
    age_range: AgeRange | None
    native_language: str
    app_language: str
    target_language: str | None
    proficiency: str | None
    study_time: str | None
    timezone: str | None
    interests: list[str]
    daily_goal: int
    streak: int
    #: The client derives level and progress from this single integer.
    xp: int
    last_studied_on: date | None
    onboarded: bool
    created_at: datetime


class CompleteOnboardingIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    username: str | None = Field(default=None, max_length=USERNAME_MAX_LENGTH)
    age_range: AgeRange | None = None
    native_language: str = Field(min_length=1, max_length=64)
    target_language: str | None = Field(default=None, max_length=64)
    #: A free-form key from the client's kProficiencyLevels — not enumerated
    #: server-side, because that list is a product surface that changes.
    proficiency: str | None = Field(default=None, max_length=32)
    study_time: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    interests: list[InterestTopic] = Field(default_factory=list)
    daily_goal: DailyGoal = 10


class UpdateProfileIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, max_length=USERNAME_MAX_LENGTH)
    age_range: AgeRange | None = None
    native_language: str | None = Field(default=None, min_length=1, max_length=64)
    app_language: str | None = Field(default=None, min_length=1, max_length=64)
    target_language: str | None = Field(default=None, max_length=64)
    proficiency: str | None = Field(default=None, max_length=32)
    study_time: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    interests: list[InterestTopic] | None = None
    daily_goal: DailyGoal | None = None


class UsernameAvailableOut(BaseModel):
    available: bool
