"""bank the streak once a day, and say which reviews were scheduled

The streak used to advance on the first graded card of any day and was written
only there, which made it wrong in two directions at once. It was too easy to
earn — one card held it — and it was never brought up to date on a day nobody
studied, so a learner who missed two days kept seeing the old number until
their next review silently reset it to 1. They were never told they had lost
it, and the figure on screen while they could still have saved it was a lie.

Three columns answer that.

``users.streak_last_day`` is the last day that *counted* — banked or rested —
and is what the consecutive-day arithmetic measures from. It is deliberately
not ``last_studied_on``, which stays and keeps its own meaning: the last day
anything at all was reviewed. One card is a study day and is not necessarily a
streak day, and conflating the two is the whole bug.

``users.streak_banked_on`` is the last day the goal was met. It is the
once-a-day lock: ``bank_day`` guards on it in a single statement, so two
sessions finishing in the same second — a phone and the PWA — cannot both
collect, and the statement's own rowcount is the only trustworthy answer to
"was this the request that crossed it".

``daily_deck_activity.due_reviews`` counts the answers the scheduler had
actually asked for. The streak's light-day path is "they cleared the queue",
and that cannot be re-derived after the fact: reviewing a card rewrites its
``due_at``, so what was due earlier today is already gone. Without this column
the path would also fire on a brand-new deck, whose cards are all tomorrow's
work and whose queue is therefore empty from the moment it is created — one
review would bank the day, which is the behaviour being replaced.

Backfilled, so nobody's streak moves on deploy: under the old rule any study
day was a streak day, which makes ``last_studied_on`` the best available truth
for both new dates. ``due_reviews`` starts at 0 for past days rather than being
reconstructed from ``word_reviews`` — the log has the pre-review due date and
could answer it, but a backfill would be aggregating the review log to settle
days that are already closed, and every one of those days has its streak
recorded already.

Revision ID: a1c47e93b2f5
Revises: e7a3c15d4f80
Create Date: 2026-08-16 19:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c47e93b2f5"
down_revision: str | None = "e7a3c15d4f80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("streak_last_day", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("streak_banked_on", sa.Date(), nullable=True))
    op.execute(
        "UPDATE users SET streak_last_day = last_studied_on, "
        "streak_banked_on = last_studied_on"
    )
    op.add_column(
        "daily_deck_activity",
        sa.Column("due_reviews", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("daily_deck_activity", "due_reviews")
    op.drop_column("users", "streak_banked_on")
    op.drop_column("users", "streak_last_day")
