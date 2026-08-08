"""Experience points: what earns them, and what they buy.

Mirrors the client's ``lib/models/progress_rewards.dart`` exactly. A
disagreement here shows up as a level that changes when the learner switches
device, which is worse than having no levels at all.
"""

from __future__ import annotations

from enum import StrEnum


class XpAction(StrEnum):
    """Something the learner did that is worth experience."""

    GRADE_WORD = "grade_word"
    DRILL_CORRECT = "drill_correct"
    #: Turning up to be tested on your weakest words is the behaviour worth
    #: rewarding, so a wrong answer still pays — just less.
    DRILL_WRONG = "drill_wrong"
    ADD_WORD = "add_word"
    FINISH_SESSION = "finish_session"
    DAILY_GOAL = "daily_goal"

    @property
    def points(self) -> int:
        return _POINTS[self]


_POINTS: dict[XpAction, int] = {
    XpAction.GRADE_WORD: 6,
    XpAction.DRILL_CORRECT: 8,
    XpAction.DRILL_WRONG: 3,
    XpAction.ADD_WORD: 5,
    XpAction.FINISH_SESSION: 15,
    XpAction.DAILY_GOAL: 25,
}


def total_xp_for(level: int) -> int:
    """Cumulative XP needed to reach ``level``.

    A formula rather than a table, so there is no ceiling to maintain: level 2
    at 100, level 3 at 300, level 4 at 600.
    """
    return 0 if level <= 1 else 50 * level * (level - 1)


def level_for(xp: int) -> int:
    """The level ``xp`` has bought."""
    if xp <= 0:
        return 1
    level = 1
    while total_xp_for(level + 1) <= xp:
        level += 1
    return level
