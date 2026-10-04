"""Link challenge ORM model. One row per user and kind; asking again replaces it."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.infrastructure.db.types import UTCDateTime


class LinkChallengeModel(Base):
    __tablename__ = "account_link_challenges"

    #: The key *is* the limit on outstanding codes: a second request overwrites
    #: the first, so the table holds at most two rows per account and needs no
    #: sweeper. Cascades with the user — a code for a deleted account proves
    #: nothing to anyone.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    #: What the code was sent to, already normalised. Checked again at verify,
    #: so a code that proved one address cannot attach a different one.
    target: Mapped[str] = mapped_column(String(320), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    attempts_left: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
