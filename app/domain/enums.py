"""Domain enumerations shared across entities and services."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class AuthMethod(StrEnum):
    PHONE = "phone"
    GOOGLE = "google"


class AgeRange(StrEnum):
    """Age buckets offered during onboarding (mirrors the app UI)."""

    TEEN = "13-17"
    YOUNG_ADULT = "18-24"
    ADULT_25 = "25-34"
    ADULT_35 = "35-44"
    ADULT_45 = "45-54"
    ADULT_55 = "55-64"
    SENIOR = "65+"


class LeitnerBox(IntEnum):
    """Leitner boxes 1..5. Higher box = better remembered, longer interval."""

    NEW = 1
    LEARNING = 2
    FAMILIAR = 3
    KNOWN = 4
    MASTERED = 5

    @property
    def label(self) -> str:
        return {
            1: "New",
            2: "Learning",
            3: "Familiar",
            4: "Known",
            5: "Mastered",
        }[self.value]


class ReviewGrade(StrEnum):
    """How well the learner recalled a card during study."""

    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"
