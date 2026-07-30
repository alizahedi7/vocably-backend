"""Unit tests for review history's domain layer — pure, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.entities.review_event import MAX_LATENCY_MS, ReviewEvent
from app.domain.entities.word import Word
from app.domain.enums import LeitnerBox, ReviewGrade

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def make_word(**overrides: object) -> Word:
    fields: dict[str, object] = {
        "user_id": uuid4(),
        "deck_id": uuid4(),
        "term": "improve",
        "meaning": "to get better",
        "due_at": NOW,
    }
    fields.update(overrides)
    return Word(**fields)  # type: ignore[arg-type]


# ── ReviewGrade ordinals ─────────────────────────────────────
def test_grade_ordinals_are_the_frozen_wire_format() -> None:
    # These are persisted in word_reviews rows that outlive any deploy.
    # If this test needs changing, the change is almost certainly wrong.
    assert ReviewGrade.AGAIN.ordinal == 0
    assert ReviewGrade.HARD.ordinal == 1
    assert ReviewGrade.GOOD.ordinal == 2
    assert ReviewGrade.EASY.ordinal == 3


@pytest.mark.parametrize("grade", list(ReviewGrade))
def test_ordinal_round_trips(grade: ReviewGrade) -> None:
    assert ReviewGrade.from_ordinal(grade.ordinal) is grade


def test_unknown_ordinal_is_rejected() -> None:
    # A row written by a newer deploy must not silently decode as some other grade.
    with pytest.raises(ValueError, match="Unknown review grade ordinal"):
        ReviewGrade.from_ordinal(99)


def test_only_again_is_a_lapse() -> None:
    assert ReviewGrade.AGAIN.is_lapse
    assert not ReviewGrade.HARD.is_lapse
    assert not ReviewGrade.GOOD.is_lapse
    assert not ReviewGrade.EASY.is_lapse


# ── Word.apply_review ────────────────────────────────────────
def test_apply_review_updates_scheduling_state() -> None:
    word = make_word()
    due = NOW + timedelta(days=2)

    word.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, due, NOW)

    assert word.box is LeitnerBox.LEARNING
    assert word.due_at == due
    assert word.review_count == 1
    assert word.last_reviewed_at == NOW
    assert word.updated_at == NOW
    assert word.last_grade is ReviewGrade.GOOD


def test_first_reviewed_at_is_stamped_once_and_never_moves() -> None:
    word = make_word()
    later = NOW + timedelta(days=5)

    word.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, later, NOW)
    word.apply_review(ReviewGrade.GOOD, LeitnerBox.FAMILIAR, later, later)

    assert word.first_reviewed_at == NOW  # the start of the learning curve


def test_lapse_count_and_streak_track_again() -> None:
    word = make_word()

    word.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW, NOW)
    word.apply_review(ReviewGrade.HARD, LeitnerBox.NEW, NOW, NOW)
    assert word.lapse_count == 0
    assert word.consecutive_correct == 2  # `hard` is a pass, not a lapse

    word.apply_review(ReviewGrade.AGAIN, LeitnerBox.NEW, NOW, NOW)
    assert word.lapse_count == 1
    assert word.consecutive_correct == 0

    word.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW, NOW)
    assert word.consecutive_correct == 1


def test_mastered_at_is_stamped_on_first_entry_to_box_five_and_survives_a_lapse() -> None:
    word = make_word()
    mastered = NOW + timedelta(days=10)
    relapsed = NOW + timedelta(days=20)

    word.apply_review(ReviewGrade.EASY, LeitnerBox.FAMILIAR, NOW, NOW)
    assert word.mastered_at is None

    word.apply_review(ReviewGrade.EASY, LeitnerBox.MASTERED, mastered, mastered)
    assert word.mastered_at == mastered
    assert word.time_to_mastery == mastered - NOW

    # Forgetting it later does not un-master it — time-to-mastery is a fact
    # about the past, not a description of the card's current strength.
    word.apply_review(ReviewGrade.AGAIN, LeitnerBox.NEW, relapsed, relapsed)
    assert word.mastered_at == mastered


def test_time_to_mastery_is_none_until_mastered() -> None:
    word = make_word()
    assert word.time_to_mastery is None
    word.apply_review(ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW, NOW)
    assert word.time_to_mastery is None


def test_lapse_rate_ranks_difficulty_independently_of_review_volume() -> None:
    word = make_word()
    assert word.lapse_rate == 0.0  # never reviewed — no signal, not "easy"

    for grade in (ReviewGrade.AGAIN, ReviewGrade.GOOD, ReviewGrade.AGAIN, ReviewGrade.GOOD):
        word.apply_review(grade, LeitnerBox.NEW, NOW, NOW)
    assert word.lapse_rate == 0.5


# ── ReviewEvent.from_review ──────────────────────────────────
def test_from_review_captures_state_before_the_card_is_mutated() -> None:
    word = make_word(box=LeitnerBox.FAMILIAR, due_at=NOW - timedelta(days=3))
    word.last_reviewed_at = NOW - timedelta(days=7)

    event = ReviewEvent.from_review(word, ReviewGrade.GOOD, LeitnerBox.KNOWN, NOW)
    word.apply_review(ReviewGrade.GOOD, LeitnerBox.KNOWN, NOW + timedelta(days=9), NOW)

    assert event.box_before is LeitnerBox.FAMILIAR  # not clobbered by apply_review
    assert event.box_after is LeitnerBox.KNOWN
    assert event.elapsed_seconds == 7 * 86_400
    assert event.overdue_seconds == 3 * 86_400  # reviewed 3 days late
    assert event.user_id == word.user_id
    assert event.deck_id == word.deck_id
    assert event.grade is ReviewGrade.GOOD


def test_first_ever_review_has_no_elapsed_time() -> None:
    word = make_word()
    event = ReviewEvent.from_review(word, ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW)
    assert event.elapsed_seconds is None


def test_reviewing_early_records_negative_overdue() -> None:
    word = make_word(due_at=NOW + timedelta(days=2))
    event = ReviewEvent.from_review(word, ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW)
    assert event.overdue_seconds == -2 * 86_400


def test_client_supplied_latency_is_clamped() -> None:
    word = make_word()

    assert (
        ReviewEvent.from_review(word, ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW).latency_ms is None
    )
    assert (
        ReviewEvent.from_review(
            word, ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW, latency_ms=-5
        ).latency_ms
        == 0
    )
    # A backgrounded app must not drag every latency average with it.
    assert (
        ReviewEvent.from_review(
            word, ReviewGrade.GOOD, LeitnerBox.LEARNING, NOW, latency_ms=99_999_999
        ).latency_ms
        == MAX_LATENCY_MS
    )


def test_event_is_immutable() -> None:
    word = make_word()
    event = ReviewEvent.from_review(word, ReviewGrade.AGAIN, LeitnerBox.NEW, NOW)
    assert event.is_lapse
    with pytest.raises(AttributeError):
        event.grade = ReviewGrade.EASY  # type: ignore[misc]
