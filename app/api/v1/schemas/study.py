"""Study/review request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel

from app.api.v1.schemas.word import WordOut
from app.application.dto import StudyOverview
from app.domain.enums import LeitnerBox, ReviewGrade


class GradeIn(BaseModel):
    grade: ReviewGrade


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
    due_deck_count: int
    estimated_minutes: int
    streak: int
    memory_strength: MemoryStrengthOut

    @classmethod
    def from_dto(cls, dto: StudyOverview) -> StudyOverviewOut:
        return cls(
            due_count=dto.due_count,
            total_count=dto.total_count,
            learned_count=dto.learned_count,
            due_deck_count=dto.due_deck_count,
            estimated_minutes=dto.estimated_minutes,
            streak=dto.streak,
            memory_strength=MemoryStrengthOut(
                total=dto.memory_strength.total,
                distribution=[
                    BoxCountOut(box=b.box, label=b.label, count=b.count)
                    for b in dto.memory_strength.distribution
                ],
            ),
        )


class StudySessionOut(BaseModel):
    """The queue of cards to review this session."""

    count: int
    words: list[WordOut]
