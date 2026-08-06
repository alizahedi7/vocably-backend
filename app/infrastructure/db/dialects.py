"""Dialect differences that cannot be expressed in portable SQLAlchemy Core.

There is exactly one so far: ``INSERT … ON CONFLICT`` lives on the dialect
module rather than on ``sqlalchemy.insert``. Postgres and SQLite both speak it
and expose the same ``on_conflict_do_update`` signature, so the upsert has one
body and this table is the entire difference between them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

#: Production is Postgres; the test suite builds its schema on in-memory SQLite
#: (see ``tests/api/conftest.py``), so both have to work. A dialect that is not
#: here has no ``ON CONFLICT`` and must fail loudly rather than degrade to an
#: insert that raises on the second grade of a word.
_INSERT_BUILDERS: dict[str, Callable[..., Any]] = {
    "postgresql": postgresql.insert,
    "sqlite": sqlite.insert,
}


def upsert_insert(session: AsyncSession) -> Callable[..., Any]:
    """The ``insert()`` constructor that supports ``on_conflict_do_update``."""
    name = session.get_bind().dialect.name
    try:
        return _INSERT_BUILDERS[name]
    except KeyError:
        raise NotImplementedError(f"No ON CONFLICT support wired for dialect {name!r}") from None
