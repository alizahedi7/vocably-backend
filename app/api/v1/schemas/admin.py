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
from app.application.services.content_admin_service import BuildJobDetail
from app.domain.entities.deck_build import DeckBuildItem, DeckBuildJob
from app.domain.entities.lexeme import Lexeme, LexemeSense
from app.domain.entities.word import Word
from app.domain.enums import AuthMethod, SenseStatus
from app.domain.repositories.lexicon_repository import LexiconStats


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


class AdminPublishDeckIn(BaseModel):
    """Curating Explore. The one mutation on an otherwise read-only surface."""

    is_public: bool = True
    #: Published by Vocably itself, which is what suppresses the author credit.
    is_official: bool = False
    category: str | None = Field(default=None, max_length=32)
    description: str | None = None
    description_fa: str | None = None


# ── Content pipeline: builds and the lexicon ─────────────────


class BuildJobOut(_CamelModel):
    """One deck build, as the builds list renders it."""

    id: UUID
    template_slug: str = Field(serialization_alias="templateSlug")
    template_version: str = Field(serialization_alias="templateVersion")
    deck_id: UUID | None = Field(serialization_alias="deckId")
    state: str
    content_version: int = Field(serialization_alias="contentVersion")
    native_language: str = Field(serialization_alias="nativeLanguage")
    items_total: int = Field(serialization_alias="itemsTotal")
    items_done: int = Field(serialization_alias="itemsDone")
    items_failed: int = Field(serialization_alias="itemsFailed")
    lexemes_reused: int = Field(serialization_alias="lexemesReused")
    lexemes_generated: int = Field(serialization_alias="lexemesGenerated")
    senses_enriched: int = Field(serialization_alias="sensesEnriched")
    ai_calls: int = Field(serialization_alias="aiCalls")
    progress_pct: int = Field(serialization_alias="progressPct")
    #: Share of resolved words that cost no generation — the number that says
    #: whether the lexicon is doing its job.
    reuse_pct: int = Field(serialization_alias="reusePct")
    last_error: str | None = Field(serialization_alias="lastError")
    started_at: datetime | None = Field(serialization_alias="startedAt")
    finished_at: datetime | None = Field(serialization_alias="finishedAt")
    created_at: datetime = Field(serialization_alias="createdAt")

    @classmethod
    def from_entity(cls, job: DeckBuildJob) -> BuildJobOut:
        return cls(
            id=job.id,
            template_slug=job.template_slug,
            template_version=job.template_version,
            deck_id=job.deck_id,
            state=job.state.value,
            content_version=job.content_version,
            native_language=job.native_language,
            items_total=job.items_total,
            items_done=job.items_done,
            items_failed=job.items_failed,
            lexemes_reused=job.lexemes_reused,
            lexemes_generated=job.lexemes_generated,
            senses_enriched=job.senses_enriched,
            ai_calls=job.ai_calls,
            progress_pct=job.progress_pct,
            reuse_pct=job.reuse_pct,
            last_error=job.last_error,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
        )


class BuildJobPageOut(_CamelModel):
    items: list[BuildJobOut]
    total: int


class BuildJobDetailOut(_CamelModel):
    job: BuildJobOut
    #: Item counts keyed by state name, for the progress bar's segments.
    states: dict[str, int]
    needs_attention: int = Field(serialization_alias="needsAttention")
    estimated_remaining_usd: float = Field(serialization_alias="estimatedRemainingUsd")

    @classmethod
    def from_dto(cls, dto: BuildJobDetail) -> BuildJobDetailOut:
        return cls(
            job=BuildJobOut.from_entity(dto.job),
            states={state.value: count for state, count in dto.states.items()},
            needs_attention=dto.needs_attention,
            estimated_remaining_usd=dto.estimated_remaining_usd,
        )


class BuildCardOut(_CamelModel):
    """The card an item produced — what a reviewer is actually judging.

    A row saying "chosen by: first" is unreviewable on its own; whether that was
    the right sense is a question about the meaning, definition and example it
    picked.
    """

    id: UUID
    term: str
    meaning: str
    definition: str | None
    example: str | None
    sense_label: str | None = Field(serialization_alias="senseLabel")
    #: ``null`` = never looked up; ``""`` = the dictionary has no IPA for it.
    phonetic: str | None

    @classmethod
    def from_entity(cls, word: Word) -> BuildCardOut:
        return cls(
            id=word.id,
            term=word.term,
            meaning=word.meaning,
            definition=word.definition,
            example=word.example,
            sense_label=word.sense_label,
            phonetic=word.phonetic,
        )


class BuildItemOut(_CamelModel):
    id: UUID
    position: int
    unit_label: str = Field(serialization_alias="unitLabel")
    source_term: str = Field(serialization_alias="sourceTerm")
    state: str
    lexeme_id: UUID | None = Field(serialization_alias="lexemeId")
    sense_id: UUID | None = Field(serialization_alias="senseId")
    word_id: UUID | None = Field(serialization_alias="wordId")
    selection: str | None
    selection_score: float | None = Field(serialization_alias="selectionScore")
    attempts: int
    enriched: bool
    last_error: str | None = Field(serialization_alias="lastError")
    hint: str
    card: BuildCardOut | None = None

    @classmethod
    def from_entity(cls, item: DeckBuildItem, card: Word | None = None) -> BuildItemOut:
        hint = item.hint
        rendered = f"{hint.part_of_speech} · {hint.context}" if hint.is_pinned else hint.gloss
        return cls(
            id=item.id,
            position=item.position,
            unit_label=item.unit_label,
            source_term=item.source_term,
            state=item.state.value,
            lexeme_id=item.lexeme_id,
            sense_id=item.sense_id,
            word_id=item.word_id,
            selection=item.selection.value if item.selection else None,
            selection_score=item.selection_score,
            attempts=item.attempts,
            enriched=item.enriched,
            last_error=item.last_error,
            hint=rendered,
            card=BuildCardOut.from_entity(card) if card else None,
        )


class BuildItemPageOut(_CamelModel):
    items: list[BuildItemOut]
    total: int


class LexiconStatsOut(_CamelModel):
    lexemes: int
    senses: int
    translations: int
    needs_review: int = Field(serialization_alias="needsReview")
    approved: int
    rejected: int
    #: Senses an older pipeline wrote. Reported, never acted on automatically —
    #: regeneration is always an explicit, bounded command.
    stale: int
    current_content_version: int = Field(serialization_alias="currentContentVersion")

    @classmethod
    def from_dto(cls, dto: LexiconStats, *, current_version: int) -> LexiconStatsOut:
        return cls(
            lexemes=dto.lexemes,
            senses=dto.senses,
            translations=dto.translations,
            needs_review=dto.needs_review,
            approved=dto.approved,
            rejected=dto.rejected,
            stale=dto.stale,
            current_content_version=current_version,
        )


class SenseOut(_CamelModel):
    id: UUID
    lexeme_id: UUID = Field(serialization_alias="lexemeId")
    sense_key: str = Field(serialization_alias="senseKey")
    # Named `audience` on this model only: `register` collides with the
    # `ABCMeta.register` that Pydantic's metaclass inherits, and Pydantic warns
    # about the shadowing. The wire name stays `register`.
    audience: str = Field(serialization_alias="register")
    position: int
    part_of_speech: str = Field(serialization_alias="partOfSpeech")
    context: str
    definition: str
    example: str
    status: str
    content_version: int = Field(serialization_alias="contentVersion")
    provider: str
    model: str
    source: str
    #: ``{"Persian": "اداره کردن"}`` — one entry per language this sense has a
    #: headline in. A sense with none is a miss, not an empty card.
    translations: dict[str, str]
    created_at: datetime = Field(serialization_alias="createdAt")

    @classmethod
    def from_entity(cls, sense: LexemeSense) -> SenseOut:
        return cls(
            id=sense.id,
            lexeme_id=sense.lexeme_id,
            sense_key=sense.sense_key,
            audience=sense.register,
            position=sense.position,
            part_of_speech=sense.part_of_speech,
            context=sense.context,
            definition=sense.definition,
            example=sense.example,
            status=sense.status.value,
            content_version=sense.content_version,
            provider=sense.provider,
            model=sense.model,
            source=sense.source.value,
            translations={t.native_language: t.native_meaning for t in sense.translations},
            created_at=sense.created_at,
        )


class LexemeOut(_CamelModel):
    id: UUID
    lemma: str
    language: str
    display_term: str = Field(serialization_alias="displayTerm")
    #: ``null`` means never looked up; ``""`` means the dictionary has no IPA for
    #: this word. The client renders both as nothing and never has to tell them
    #: apart — but the admin screen does, so the distinction survives the wire.
    phonetic: str | None
    senses: list[SenseOut]
    created_at: datetime = Field(serialization_alias="createdAt")

    @classmethod
    def from_entity(cls, lexeme: Lexeme) -> LexemeOut:
        return cls(
            id=lexeme.id,
            lemma=lexeme.lemma,
            language=lexeme.language,
            display_term=lexeme.display_term,
            phonetic=lexeme.phonetic,
            senses=[SenseOut.from_entity(s) for s in lexeme.senses],
            created_at=lexeme.created_at,
        )


class LexemePageOut(_CamelModel):
    items: list[LexemeOut]
    total: int


class ReviewQueueRowOut(_CamelModel):
    """One flagged sense with enough of its headword to judge it by."""

    lexeme_id: UUID = Field(serialization_alias="lexemeId")
    term: str
    sense: SenseOut


class ReviewQueuePageOut(_CamelModel):
    items: list[ReviewQueueRowOut]
    total: int


class SenseUpdateIn(BaseModel):
    """An admin's edit. Every field optional; omitted means "leave it alone"."""

    status: SenseStatus | None = None
    definition: str | None = Field(default=None, max_length=400)
    example: str | None = Field(default=None, max_length=300)
    context: str | None = Field(default=None, max_length=40)
    native_language: str | None = Field(default=None, max_length=64)
    native_meaning: str | None = Field(default=None, max_length=200)


class BuildItemSenseIn(BaseModel):
    """Point a built card at a different sense of the same word."""

    sense_id: UUID


class RetryOut(_CamelModel):
    requeued: int
