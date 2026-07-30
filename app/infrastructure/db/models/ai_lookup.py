"""ORM models for the shared AI lookup cache.

Two tables rather than one, because the two halves of a lookup have different
owners. The *senses* belong to the resolved term and are identical for everyone
who reaches it; the *status* and *notice* belong to what the learner actually
typed ("Showing results for 'run'"). Splitting them means a typo, a sentence, and
the correctly spelled word share one paid-for entry instead of buying three.

Neither table carries a ``user_id``, and nothing here is scoped to an owner —
this is a dictionary the whole platform shares, not user data. See
:mod:`app.application.ports.lookup_cache` for the full contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base
from app.infrastructure.db.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.db.types import UTCDateTime

#: JSONB on Postgres (compact, indexable, TOAST-compressed); plain JSON on the
#: SQLite the test suite runs against by default.
CachePayload = JSON().with_variant(JSONB(), "postgresql")


class AILookupEntryModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The expensive half: every sense a provider returned for one term."""

    __tablename__ = "ai_lookup_entries"
    __table_args__ = (
        # Sweeping retired prompt versions is the only bulk operation this table
        # ever sees; everything else arrives by hash.
        Index("ix_ai_lookup_entries_prompt_version", "prompt_version"),
    )

    #: sha256 of (prompt_version, native_language, age_bucket, normalized term).
    #: Unique and fixed-width, so the hot index stays narrow and collation-neutral
    #: across every script the app accepts. See ``LookupCacheKey.digest``.
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    #: The resolved term as the provider spelled it — this is what the learner
    #: sees, so it keeps the provider's casing rather than the normalized form.
    term: Mapped[str] = mapped_column(String(255), nullable=False)
    native_language: Mapped[str] = mapped_column(String(64), nullable=False)
    age_bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: The senses, in the storage shape owned by ``lookup_cache_payload``.
    payload: Mapped[dict[str, Any]] = mapped_column(CachePayload, nullable=False)

    #: Which provider and model produced this. Not part of the key — swapping
    #: models is not a reason to re-buy the corpus — but essential for telling
    #: where a bad card came from.
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    #: Updated by ``SqlAlchemyLookupCacheRepository.get`` on every hit that
    #: resolves to this entry — i.e. how many times the cache has stood in for
    #: a provider call. Best-effort: a failed update never turns a hit into a
    #: miss, so this can lag reality but never overcounts.
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AILookupAliasModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The cheap half: one row per distinct thing a learner typed."""

    __tablename__ = "ai_lookup_aliases"
    __table_args__ = (
        Index("ix_ai_lookup_aliases_prompt_version", "prompt_version"),
        # Only for the periodic sweep of expired `unsupported` rows.
        Index("ix_ai_lookup_aliases_expires_at", "expires_at"),
    )

    #: sha256 of (prompt_version, native_language, age_bucket, normalized input).
    alias_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    normalized_input: Mapped[str] = mapped_column(String(255), nullable=False)
    native_language: Mapped[str] = mapped_column(String(64), nullable=False)
    age_bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: NULL for `unsupported`, which has senses to show nobody. Every other
    #: status points at the entry holding the deck.
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_lookup_entries.id", ondelete="CASCADE"), index=True
    )

    #: How the provider interpreted this input, and the notice explaining it.
    #: Both belong to the input rather than the term: two different typos of
    #: "run" share an entry but each keeps its own "Showing results for …".
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    notice: Mapped[str | None] = mapped_column(Text)
    #: Denormalised from the entry so an `unsupported` row — which has no entry —
    #: can still be reconstructed, and so a hit is one indexed read either way.
    resolved_term: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Set only for `unsupported`. Real words do not expire; unintelligible input
    #: does, so retry-spam is absorbed without the table filling with junk.
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
