"""Unit tests for one learner's study state — pure, no I/O.

These moved off ``Word`` with the ``words``/``word_progress`` split. The
assertions are unchanged: the scheduling bookkeeping is the same function, it
just now belongs to the learner rather than to the card.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.domain.entities.word_progress import WordProgress
from app.domain.enums import LeitnerBox, ReviewGrade

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def make_progress(**overrides: object) -> WordProgress:
    fields: dict[str, object] = {
        "user_id": uuid4(),
        "word_id": uuid4(),
        "deck_id": uuid4(),
        "due_at": NOW,
    }
    fields.update(overrides)
    return WordProgress(**fields)  # type: ignore[arg-type]


# ── the default a missing row reads as ───────────────────────
def test_unstudied_reads_as_a_new_card_due_now() -> None:
    # Progress rows are created lazily, so this is what most words in a freshly
    # shared deck look like — and it must be indistinguishable from a real row.
    progress = WordProgress.unstudied(uuid4(), uuid4(), uuid4(), NOW)

    assert progress.box is LeitnerBox.NEW
    assert progress.due_at == NOW
    assert progress.is_due(NOW)
    assert progress.review_count == 0
    assert progress.lapse_count == 0
    assert progress.consecutive_correct == 0
    assert progress.first_reviewed_at is None
    assert progress.mastered_at is None
    assert progress.last_grade is None


# ── WordProgress.apply_review ────────────────────────────────
def test_apply_review_updates_scheduling_state() -> None:
    progress = make_progress()
    due = NOW + timedelta(days=3)

    progress.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, due, NOW)

    assert progress.box is LeitnerBox.LEARNING
    assert progress.due_at == due
    assert progress.review_count == 1
    assert progress.last_reviewed_at == NOW
    assert progress.updated_at == NOW
    assert progress.last_grade is ReviewGrade.GOOD


def test_first_reviewed_at_is_stamped_once_and_never_moves() -> None:
    progress = make_progress()
    later = NOW + timedelta(days=1)

    progress.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, later, NOW)
    progress.apply_review(ReviewGrade.GOOD, LeitnerBox.FAMILIAR, later, later)

    assert progress.first_reviewed_at == NOW  # the start of the learning curve


def test_lapse_count_and_streak_track_again() -> None:
    progress = make_progress()

    progress.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW, NOW)
    progress.apply_review(ReviewGrade.HARD, LeitnerBox.NEW, NOW, NOW)
    assert progress.lapse_count == 0
    assert progress.consecutive_correct == 2  # `hard` is a pass, not a lapse

    progress.apply_review(ReviewGrade.AGAIN, LeitnerBox.NEW, NOW, NOW)
    assert progress.lapse_count == 1
    assert progress.consecutive_correct == 0

    progress.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW, NOW)
    assert progress.consecutive_correct == 1


def test_mastered_at_is_stamped_on_first_entry_to_box_five_and_survives_a_lapse() -> None:
    progress = make_progress()
    mastered = NOW + timedelta(days=10)

    progress.apply_review(ReviewGrade.EASY, LeitnerBox.FAMILIAR, NOW, NOW)
    assert progress.mastered_at is None

    progress.apply_review(ReviewGrade.EASY, LeitnerBox.MASTERED, mastered, mastered)
    assert progress.mastered_at == mastered
    assert progress.time_to_mastery == mastered - NOW

    relapsed = mastered + timedelta(days=1)
    progress.apply_review(ReviewGrade.AGAIN, LeitnerBox.NEW, relapsed, relapsed)
    assert progress.mastered_at == mastered


def test_apply_review_reports_the_transition_into_mastery_exactly_once() -> None:
    # The signal the per-deck activity rollup increments on. Reporting it twice
    # would double-count a learner who lapsed out of box 5 and came back.
    progress = make_progress()

    assert not progress.apply_review(ReviewGrade.GOOD, LeitnerBox.KNOWN, NOW, NOW).became_mastered
    assert progress.apply_review(ReviewGrade.GOOD, LeitnerBox.MASTERED, NOW, NOW).became_mastered

    progress.apply_review(ReviewGrade.AGAIN, LeitnerBox.NEW, NOW, NOW)
    again = progress.apply_review(ReviewGrade.GOOD, LeitnerBox.MASTERED, NOW, NOW)
    assert not again.became_mastered


def test_time_to_mastery_is_none_until_mastered() -> None:
    progress = make_progress()
    assert progress.time_to_mastery is None
    progress.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW, NOW)
    assert progress.time_to_mastery is None


def test_lapse_rate_ranks_difficulty_independently_of_review_volume() -> None:
    progress = make_progress()
    assert progress.lapse_rate == 0.0  # never reviewed — no signal, not "easy"

    for grade in (ReviewGrade.AGAIN, ReviewGrade.GOOD, ReviewGrade.AGAIN, ReviewGrade.GOOD):
        progress.apply_review(grade, LeitnerBox.NEW, NOW, NOW)
    assert progress.lapse_rate == 0.5
