"""Domain enumerations shared across entities and services."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class AuthMethod(StrEnum):
    PHONE = "phone"
    GOOGLE = "google"


class DeckRole(StrEnum):
    """What someone may do with a deck they belong to.

    Three roles rather than a permission matrix: a deck has words and members,
    so there are only two things to be allowed to change. Mirrors the client's
    ``DeckRole`` in ``lib/models/deck_member.dart``.
    """

    #: Made it. Edits words, invites, changes roles, and sees every member's
    #: progress in detail — which is also what makes a teacher a teacher.
    OWNER = "owner"
    #: Adds and changes words. Sees only summaries of other members.
    EDITOR = "editor"
    #: Studies it, and cannot change a word. The default for a class: a student
    #: adding words to the teacher's deck is rarely what was meant.
    VIEWER = "viewer"

    @classmethod
    def parse(cls, raw: str | None) -> DeckRole:
        """Read a role off the wire, failing closed.

        An unknown or missing role is the *least* privileged one, in both
        directions of the wire — the client's ``DeckRole.parse`` does the same.
        A role this deploy does not recognise must never widen access.
        """
        if raw is None:
            return cls.VIEWER
        try:
            return cls(raw)
        except ValueError:
            return cls.VIEWER

    @property
    def can_edit_words(self) -> bool:
        return self is not DeckRole.VIEWER

    @property
    def can_manage_members(self) -> bool:
        """Inviting, removing, changing roles, and seeing per-member detail."""
        return self is DeckRole.OWNER


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

    @property
    def ordinal(self) -> int:
        """The compact form persisted in the review log.

        Grades are stored as ``smallint`` rather than their names: two bytes
        instead of seven on the highest-volume table in the schema, and ordered,
        so "was this at least a pass?" is a range predicate.

        These numbers are written into ``word_reviews`` rows that outlive any
        deploy, so they MUST NEVER be reassigned. A new grade takes the next
        free ordinal; a retired grade's ordinal stays retired.
        """
        return _GRADE_ORDINALS[self]

    @classmethod
    def from_ordinal(cls, value: int) -> ReviewGrade:
        """Inverse of :attr:`ordinal`. Raises ``ValueError`` on unknown values."""
        try:
            return _GRADES_BY_ORDINAL[value]
        except KeyError:
            raise ValueError(f"Unknown review grade ordinal: {value}") from None

    @property
    def is_lapse(self) -> bool:
        """Whether this grade counts as forgetting the card.

        Only ``again`` does. ``hard`` is a successful recall that merely felt
        effortful — counting it as a lapse would make the difficulty signal
        (see ``Word.lapse_count``) mostly noise.
        """
        return self is ReviewGrade.AGAIN


#: Frozen wire format — see :attr:`ReviewGrade.ordinal`. Never renumber.
_GRADE_ORDINALS: dict[ReviewGrade, int] = {
    ReviewGrade.AGAIN: 0,
    ReviewGrade.HARD: 1,
    ReviewGrade.GOOD: 2,
    ReviewGrade.EASY: 3,
}
_GRADES_BY_ORDINAL: dict[int, ReviewGrade] = {v: k for k, v in _GRADE_ORDINALS.items()}
