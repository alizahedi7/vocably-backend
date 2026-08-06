"""Admin dashboard response schemas.

Field names use ``camelCase`` aliases to match the standalone admin dashboard's
TypeScript types (``vocably-admin``) so its data layer can consume these
responses verbatim. ``populate_by_name`` keeps the snake_case builders below
readable while serialising by alias.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.dto import (
    AdminCacheAliasRow,
    AdminCacheEntryRow,
    AdminCacheOverview,
    AdminDeckRow,
    AdminOverview,
    AdminUserRow,
    AdminWordRow,
    AuthMethodCount,
    DailyCount,
)
from app.domain.enums import AuthMethod


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class OverviewOut(_CamelModel):
    total_users: int = Field(serialization_alias="totalUsers")
    new_users_last_7d: int = Field(serialization_alias="newUsersLast7d")
    total_decks: int = Field(serialization_alias="totalDecks")
    total_words: int = Field(serialization_alias="totalWords")
    active_users_last_7d: int = Field(serialization_alias="activeUsersLast7d")
    onboarded_rate: float = Field(serialization_alias="onboardedRate")

    @classmethod
    def from_dto(cls, dto: AdminOverview) -> OverviewOut:
        return cls(
            total_users=dto.total_users,
            new_users_last_7d=dto.new_users_last_7d,
            total_decks=dto.total_decks,
            total_words=dto.total_words,
            active_users_last_7d=dto.active_users_last_7d,
            onboarded_rate=dto.onboarded_rate,
        )


class TimeSeriesPointOut(_CamelModel):
    date: date
    value: int

    @classmethod
    def from_dto(cls, dto: DailyCount) -> TimeSeriesPointOut:
        return cls(date=dto.day, value=dto.count)


class AuthMethodBreakdownOut(_CamelModel):
    method: AuthMethod
    count: int

    @classmethod
    def from_dto(cls, dto: AuthMethodCount) -> AuthMethodBreakdownOut:
        return cls(method=dto.method, count=dto.count)


class AdminUserOut(_CamelModel):
    id: UUID
    name: str
    phone: str | None
    email: str | None
    auth_method: AuthMethod = Field(serialization_alias="authMethod")
    onboarded: bool
    streak: int
    deck_count: int = Field(serialization_alias="deckCount")
    word_count: int = Field(serialization_alias="wordCount")
    registered_at: datetime = Field(serialization_alias="registeredAt")
    last_login_at: datetime | None = Field(serialization_alias="lastLoginAt")

    @classmethod
    def from_dto(cls, dto: AdminUserRow) -> AdminUserOut:
        u = dto.user
        return cls(
            id=u.id,
            name=u.name,
            phone=u.phone,
            email=u.email,
            auth_method=u.auth_method,
            onboarded=u.onboarded,
            streak=u.streak,
            deck_count=dto.deck_count,
            word_count=dto.word_count,
            registered_at=u.created_at,
            last_login_at=u.last_login_at,
        )


class AdminCategoryOut(_CamelModel):
    id: UUID
    name: str
    hue: int
    owner_name: str = Field(serialization_alias="ownerName")
    word_count: int = Field(serialization_alias="wordCount")
    created_at: datetime = Field(serialization_alias="createdAt")

    @classmethod
    def from_dto(cls, dto: AdminDeckRow) -> AdminCategoryOut:
        d = dto.deck
        return cls(
            id=d.id,
            name=d.name,
            hue=d.hue,
            owner_name=dto.owner_name,
            word_count=dto.word_count,
            created_at=d.created_at,
        )


class AdminWordOut(_CamelModel):
    id: UUID
    term: str
    meaning: str
    category_name: str = Field(serialization_alias="categoryName")
    owner_name: str = Field(serialization_alias="ownerName")
    box: int
    created_at: datetime = Field(serialization_alias="createdAt")

    @classmethod
    def from_dto(cls, dto: AdminWordRow) -> AdminWordOut:
        w = dto.word
        return cls(
            id=w.id,
            term=w.term,
            meaning=w.meaning,
            category_name=dto.deck_name,
            owner_name=dto.owner_name,
            box=dto.box,
            created_at=w.created_at,
        )


class AdminCacheOverviewOut(_CamelModel):
    total_entries: int = Field(serialization_alias="totalEntries")
    total_aliases: int = Field(serialization_alias="totalAliases")
    total_hits: int = Field(serialization_alias="totalHits")
    current_prompt_version: int = Field(serialization_alias="currentPromptVersion")
    stale_entry_count: int = Field(serialization_alias="staleEntryCount")
    expired_alias_count: int = Field(serialization_alias="expiredAliasCount")

    @classmethod
    def from_dto(cls, dto: AdminCacheOverview) -> AdminCacheOverviewOut:
        return cls(
            total_entries=dto.total_entries,
            total_aliases=dto.total_aliases,
            total_hits=dto.total_hits,
            current_prompt_version=dto.current_prompt_version,
            stale_entry_count=dto.stale_entry_count,
            expired_alias_count=dto.expired_alias_count,
        )


class AdminCacheEntryOut(_CamelModel):
    id: UUID
    term: str
    native_language: str = Field(serialization_alias="nativeLanguage")
    age_bucket: str = Field(serialization_alias="ageBucket")
    prompt_version: int = Field(serialization_alias="promptVersion")
    provider: str
    model: str
    hit_count: int = Field(serialization_alias="hitCount")
    alias_count: int = Field(serialization_alias="aliasCount")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
    last_accessed_at: datetime | None = Field(serialization_alias="lastAccessedAt")

    @classmethod
    def from_dto(cls, dto: AdminCacheEntryRow) -> AdminCacheEntryOut:
        return cls(
            id=dto.id,
            term=dto.term,
            native_language=dto.native_language,
            age_bucket=dto.age_bucket,
            prompt_version=dto.prompt_version,
            provider=dto.provider,
            model=dto.model,
            hit_count=dto.hit_count,
            alias_count=dto.alias_count,
            created_at=dto.created_at,
            updated_at=dto.updated_at,
            last_accessed_at=dto.last_accessed_at,
        )


class AdminCacheEntryPageOut(_CamelModel):
    items: list[AdminCacheEntryOut]
    total: int


class AdminCacheAliasOut(_CamelModel):
    id: UUID
    normalized_input: str = Field(serialization_alias="normalizedInput")
    native_language: str = Field(serialization_alias="nativeLanguage")
    age_bucket: str = Field(serialization_alias="ageBucket")
    prompt_version: int = Field(serialization_alias="promptVersion")
    status: str
    notice: str | None
    resolved_term: str = Field(serialization_alias="resolvedTerm")
    expires_at: datetime | None = Field(serialization_alias="expiresAt")
    created_at: datetime = Field(serialization_alias="createdAt")

    @classmethod
    def from_dto(cls, dto: AdminCacheAliasRow) -> AdminCacheAliasOut:
        return cls(
            id=dto.id,
            normalized_input=dto.normalized_input,
            native_language=dto.native_language,
            age_bucket=dto.age_bucket,
            prompt_version=dto.prompt_version,
            status=dto.status,
            notice=dto.notice,
            resolved_term=dto.resolved_term,
            expires_at=dto.expires_at,
            created_at=dto.created_at,
        )


class AdminCacheAliasPageOut(_CamelModel):
    items: list[AdminCacheAliasOut]
    total: int
