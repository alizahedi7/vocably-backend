"""Feedback request/response schemas.

snake_case like the rest of the learner-facing v1 API; the camelCase aliasing
rule applies to ``/admin/*`` only (see ``schemas/admin.py``).

The two request bodies are validated to very different depths, deliberately —
see :mod:`app.application.services.feedback_service`. The report's ``message``
is the one field here with real bounds on it, because it is the one field a
learner typed and so the only one they could be asked to fix.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.entities.feedback import (
    MAX_REPORT_CHARS,
    MIN_REPORT_CHARS,
    AIFeedbackReason,
    AIRating,
    ClientPlatform,
    FeedbackKind,
    FeedbackReport,
)


class FeedbackReportIn(BaseModel):
    """One thing a learner wrote, plus how to reproduce it.

    Every metadata field is optional and unvalidated beyond a length ceiling.
    A client that cannot describe itself — an old build, a browser that says
    little — still gets its report stored, which matters more than the tidiness
    of the row.
    """

    #: ``bug`` | ``idea`` | ``other``. Anything else is read as ``other`` rather
    #: than refused: it is a triage label and nothing branches on it.
    kind: str = FeedbackKind.OTHER.value
    message: str = Field(min_length=MIN_REPORT_CHARS, max_length=MAX_REPORT_CHARS)

    #: As the client reports it, e.g. ``"1.5.0+11"``.
    app_version: str = Field(default="", max_length=64)
    #: ``android`` | ``ios`` | ``web``. Unknown values are stored as ``unknown``.
    platform: str = Field(default=ClientPlatform.UNKNOWN.value, max_length=32)
    #: Whatever the device says about itself.
    os_version: str = Field(default="", max_length=200)
    #: The *interface* language when the report was written — a fact about how
    #: the screen looked, and therefore about the bug.
    locale: str = Field(default="", max_length=32)


class FeedbackReportOut(BaseModel):
    """Just enough to confirm receipt.

    The client shows a thank-you, not the row: it already has everything it
    sent. What it does not have is the id, which is the one thing worth handing
    back — a learner following up can quote it.
    """

    id: UUID
    kind: FeedbackKind
    created_at: datetime

    @classmethod
    def from_entity(cls, report: FeedbackReport) -> FeedbackReportOut:
        return cls(id=report.id, kind=report.kind, created_at=report.created_at)


class AIFeedbackIn(BaseModel):
    """A thumb on one AI-written card back.

    Carries no copy of what was rated. ``lookup_id`` is
    :meth:`~app.application.ports.lookup_cache.LookupCacheKey.digest` for the
    resolved term — handed to the client on ``LookupOut.lookup_id`` — and the
    senses it names are already stored server-side, so echoing them back would
    let a client rewrite the record of what a model produced.
    """

    #: ``LookupOut.lookup_id`` from the lookup being rated.
    lookup_id: str = Field(min_length=1, max_length=64)
    #: Which card back, 0-based, as the deck was dealt.
    sense_index: int = Field(ge=0, le=99)
    #: ``up`` | ``down`` | ``none``. ``none`` withdraws a rating — what tapping
    #: the lit thumb again does — and deletes the row rather than storing an
    #: opinion nobody holds.
    rating: str = AIRating.NONE.value
    #: ``wrong_meaning`` | ``bad_example`` | ``wrong_sense``, and only ever
    #: alongside ``down``. Optional in the strongest sense: the rating is stored
    #: before the chips are even shown, so this is always a second, separate
    #: request that the learner is free never to make.
    reason: str | None = Field(default=None, max_length=32)


class AIFeedbackOut(BaseModel):
    """The verdict as stored — the client's confirmation that it landed.

    ``rating`` is ``none`` for a withdrawal, which is the one case where there
    is no row to describe.
    """

    lookup_id: str
    sense_index: int
    rating: AIRating
    reason: AIFeedbackReason | None = None
