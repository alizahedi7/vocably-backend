"""Domain enumerations shared across entities and services."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class AuthMethod(StrEnum):
    PHONE = "phone"
    GOOGLE = "google"


class AgeRange(StrEnum):
    """Age buckets offered during onboarding (mirrors the app UI).

    Values are the app's exact display strings — note the en-dash (–) in the
    numeric ranges, matching the option list the mobile client renders.
    """

    UNDER_13 = "Under 13"
    TEEN = "13–17"
    YOUNG_ADULT = "18–24"
    ADULT_25 = "25–34"
    ADULT_35 = "35–44"
    ADULT_45 = "45–54"
    ADULT_55 = "55–64"
    SENIOR = "65+"
    PREFER_NOT_TO_SHARE = "Prefer not to share"


class InterestTopic(StrEnum):
    """Topics the learner can pick during onboarding; themes AI-generated content."""

    DAILY = "daily"
    TRAVEL = "travel"
    WORK = "work"
    MOVIES = "movies"
    MUSIC = "music"
    FOOD = "food"
    TECH = "tech"
    SPORTS = "sports"
    HEALTH = "health"
    SCIENCE = "science"
    GAMING = "gaming"
    NEWS = "news"


#: Allowed values for the user's daily new-word goal.
DAILY_GOAL_CHOICES = (5, 10, 15, 20)


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
