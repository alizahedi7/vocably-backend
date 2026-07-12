"""Custom SQLAlchemy column types."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """A timezone-aware datetime that survives backends without offset storage.

    Postgres stores and returns aware datetimes natively, but SQLite (used by the
    test suite) returns naive values; comparing those against ``datetime.now(UTC)``
    raises ``TypeError``. Values are normalised to UTC on write and re-tagged with
    UTC on read, so every datetime leaving the database is aware.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
