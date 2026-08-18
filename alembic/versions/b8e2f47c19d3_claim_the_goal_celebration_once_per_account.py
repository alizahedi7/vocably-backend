"""claim the goal celebration once per account, not once per device

The daily goal is banked once per account per day and always was. What was
only ever device-local is the record of having *told* the learner: the "you
reached your goal" overlay was locked behind a `yyyy-mm-dd` stamp in each
device's own storage. A learner signed in on an Android build and in the PWA
therefore met the goal once and was congratulated twice — the phone banked the
day and celebrated, and the PWA later read `day_state: banked` on an ordinary
refresh, found nothing in *its* storage, and threw the same confetti for the
same day. Clearing site data, or a reinstall, did it again.

`users.goal_celebrated_on` is the account-level half of that lock, and it is
deliberately a second column rather than a reuse of `streak_banked_on`: those
answer different questions. `streak_banked_on` is whether the day was *won* —
it drives `day_state`, the at-risk flame and the streak arithmetic, and it must
go on saying "banked" for the rest of the day whoever has been told what.
`goal_celebrated_on` is whether anybody has been *told*. Overloading the first
would mean a client's celebration consuming the day's own state.

Claimed by `UserRepository.claim_goal_celebration`, which is `bank_day`'s
guarded statement with a different predicate: the rowcount is the only
trustworthy answer to "is this the request that gets to say so", so two devices
refreshing in the same instant cannot both collect.

**Not backfilled, on purpose.** NULL means "nobody has been told", so on the
morning after deploy every learner whose day is already banked is eligible for
exactly one celebration. Backfilling it from `streak_banked_on` would be the
tidier-looking choice and is the wrong one: it would silently swallow the
celebration of anyone who met their goal in the hours before the deploy and had
not yet seen it. One celebration too many for a handful of accounts, once, is
the cheaper error than one owed and never delivered — and the column costs
nothing on a day nobody has banked.

Revision ID: b8e2f47c19d3
Revises: a1c47e93b2f5
Create Date: 2026-08-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e2f47c19d3"
down_revision: str | None = "a1c47e93b2f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("goal_celebrated_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "goal_celebrated_on")
