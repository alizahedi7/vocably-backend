"""Study/review request/response schemas."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.v1.schemas.word import WordOut
from app.application.dto import StudyOverview
from app.application.services.study_service import CelebrationClaim
from app.domain.entities.review_event import MAX_LATENCY_MS
from app.domain.enums import LeitnerBox, ReviewGrade
from app.domain.services.streak import DayState


class SessionCompleteOut(BaseModel):
    """What finishing was worth, and where that leaves the learner."""

    awarded: int
    xp: int
    level: int


class GradeIn(BaseModel):
    #: Where the answer came from. A practice drill is worth different points
    #: from a review session, and a wrong drill answer still pays — turning up
    #: to be tested on your weakest words is the behaviour worth rewarding.
    #: Omitted by clients older than practice, which is why it defaults.
    source: Literal["session", "drill"] = "session"
    grade: ReviewGrade
    #: How long the learner spent on the card, in milliseconds. Optional and
    #: purely observational — it is recorded in the review log, never used to
    #: schedule. Clients that don't send it simply record NULL.
    latency_ms: int | None = Field(default=None, ge=0, le=MAX_LATENCY_MS)
    #: Client-generated id shared by every card graded in one sitting, so a
    #: session can be reconstructed from the log. Optional.
    session_id: UUID | None = None


class BoxCountOut(BaseModel):
    box: LeitnerBox
    label: str
    count: int


class MemoryStrengthOut(BaseModel):
    total: int
    distribution: list[BoxCountOut]


class StudyOverviewOut(BaseModel):
    due_count: int
    total_count: int
    learned_count: int
    mastered_count: int
    reviewed_today: int
    due_deck_count: int
    estimated_minutes: int
    streak: int
    daily_goal: int
    #: Additive: a client that predates this field ignores it and behaves
    #: exactly as it did, which matters because an installed Android build
    #: outlives the deploy by weeks.
    day_state: DayState
    memory_strength: MemoryStrengthOut

    @classmethod
    def from_dto(cls, dto: StudyOverview) -> StudyOverviewOut:
        return cls(
            due_count=dto.due_count,
            total_count=dto.total_count,
            learned_count=dto.learned_count,
            mastered_count=dto.mastered_count,
            reviewed_today=dto.reviewed_today,
            due_deck_count=dto.due_deck_count,
            estimated_minutes=dto.estimated_minutes,
            streak=dto.streak,
            daily_goal=dto.daily_goal,
            day_state=dto.day_state,
            memory_strength=MemoryStrengthOut(
                total=dto.memory_strength.total,
                distribution=[
                    BoxCountOut(box=b.box, label=b.label, count=b.count)
                    for b in dto.memory_strength.distribution
                ],
            ),
        )


class GoalCelebrationOut(BaseModel):
    """Whether the caller may show the "goal reached" celebration.

    ``claimed`` is true for exactly one request per account per local day, and
    only on a day that was actually banked. Every later caller — the same
    device relaunching, or the learner's other device — is refused.

    ``status`` says *which* refusal, and a client needs the difference:
    ``taken`` is final for the day, ``unbanked`` means the goal has not been
    met yet and is an ordinary "ask again". A client that treated the second as
    the first would lock itself out of the celebration it had just earned.
    """

    claimed: bool
    status: CelebrationClaim


class StudySessionOut(BaseModel):
    """The queue of cards to review this session."""

    count: int
    words: list[WordOut]
