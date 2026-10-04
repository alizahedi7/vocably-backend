"""link a phone and an email to one account

Three things, for one feature.

``users.is_email_verified`` records whether an address has been *proven* — by a
code sent to it, or by Google vouching for an address it hosts. Until now
``users.email`` was whatever a Google token said, checked by nobody, and it was
not unique.

``uq_users_verified_email`` is unique over proven addresses only. A unique
index across every row would let an address somebody merely claimed block its
real owner from linking it; the predicate is what makes "already in use" mean
"another account verified it".

``account_link_challenges`` holds the code sent to prove a second identifier.
One row per user and kind, replaced on each request, so it needs no sweeper.

**The backfill is deliberately narrow.** Only ``@gmail.com`` addresses on
Google accounts are marked verified: those are the ones Google is authoritative
for whatever the token's ``email_verified`` said, and nothing recorded that
claim at the time. Every other address stays unverified until its account next
signs in with Google or proves it with a code. Marking them all would bless
claims nobody checked — the opposite of what the column is for.

There is no ``is_phone_verified`` column: ``users.phone`` is only ever written
after a texted code, so the flag would always equal ``phone IS NOT NULL``.

Revision ID: e5b7a2c94d18
Revises: d2f5c8a1e764
Create Date: 2026-10-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b7a2c94d18"
down_revision: str | None = "d2f5c8a1e764"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Lowercased and trimmed, the form every write uses from here on. A unique
    # index compares bytes, so "Ali@x.com" beside "ali@x.com" would be two
    # addresses. No row is verified yet, so this cannot collide with anything.
    op.execute(
        """
        UPDATE users
           SET email = lower(btrim(email))
         WHERE email IS NOT NULL
           AND email <> lower(btrim(email))
        """
    )

    # The ``HAVING count(*) = 1`` is what makes this safe to run unseen: two
    # rows sharing an address would fail the index below and take the deploy
    # with it. Such a pair is left unverified and settles itself the next time
    # either account signs in.
    op.execute(
        """
        UPDATE users
           SET is_email_verified = true
         WHERE google_sub IS NOT NULL
           AND email LIKE '%@gmail.com'
           AND email IN (
               SELECT email FROM users
                WHERE email IS NOT NULL
                GROUP BY email
               HAVING count(*) = 1
           )
        """
    )

    op.create_index(
        "uq_users_verified_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("is_email_verified"),
    )
    op.create_check_constraint(
        "ck_users_verified_email_present",
        "users",
        "NOT is_email_verified OR email IS NOT NULL",
    )

    op.create_table(
        "account_link_challenges",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("target", sa.String(320), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("attempts_left", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "kind", name="pk_account_link_challenges"),
    )


def downgrade() -> None:
    # The lowercasing is not undone: the original casing was never recorded.
    op.drop_table("account_link_challenges")
    op.drop_constraint("ck_users_verified_email_present", "users", type_="check")
    op.drop_index("uq_users_verified_email", table_name="users")
    op.drop_column("users", "is_email_verified")
