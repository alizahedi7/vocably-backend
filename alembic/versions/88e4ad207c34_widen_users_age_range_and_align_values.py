"""widen users.age_range and align values

Age ranges now use the app's display strings: numeric ranges switch from
hyphen codes (``25-34``) to en-dash strings (``25–34``), and the new
``Under 13`` / ``Prefer not to share`` options require a wider column.

Revision ID: 88e4ad207c34
Revises: 90bc94071218
Create Date: 2026-07-12 22:08:13.931814
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "88e4ad207c34"
down_revision: str | None = "90bc94071218"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# old (hyphen) value -> new (en-dash) value; "65+" is unchanged.
_VALUE_MAP = {
    "13-17": "13–17",
    "18-24": "18–24",
    "25-34": "25–34",
    "35-44": "35–44",
    "45-54": "45–54",
    "55-64": "55–64",
}

_users = sa.table("users", sa.column("age_range", sa.String))


def upgrade() -> None:
    op.alter_column(
        "users",
        "age_range",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=True,
    )
    for old, new in _VALUE_MAP.items():
        op.execute(_users.update().where(_users.c.age_range == old).values(age_range=new))


def downgrade() -> None:
    for old, new in _VALUE_MAP.items():
        op.execute(_users.update().where(_users.c.age_range == new).values(age_range=old))
    # The two new options have no 16-char representation; drop them before narrowing.
    op.execute(
        _users.update()
        .where(_users.c.age_range.in_(["Under 13", "Prefer not to share"]))
        .values(age_range=None)
    )
    op.alter_column(
        "users",
        "age_range",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=True,
    )
