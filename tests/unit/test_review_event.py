"""Unit tests for review history's domain layer — pure, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.entities.review_event import MAX_LATENCY_MS, ReviewEvent
from app.domain.entities.studied_word import StudiedWord
from app.domain.entities.word import Word
from app.domain.entities.word_progress import WordProgress
from app.domain.enums import LeitnerBox, ReviewGrade

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def make_word(**progress_overrides: object) -> StudiedWord:
    """A card paired with one learner's state — what a review is captured from.

    The overrides all land on the progress half: the event records what the
    grade is about to overwrite, and none of that lives on the card any more.
    """
    user_id, word_id, deck_id = uuid4(), uuid4(), uuid4()
    fields: dict[str, object] = {
        "user_id": user_id,
        "word_id": word_id,
        "deck_id": deck_id,
        "due_at": NOW,
    }
    fields.update(progress_overrides)
    return StudiedWord(
        word=Word(
            id=word_id,
            deck_id=deck_id,
            created_by_user_id=user_id,
            term="improve",
            meaning="to get better",
        ),
        progress=WordProgress(**fields),  # type: ignore[arg-type]
    )


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


# ── ReviewEvent.from_review ──────────────────────────────────
def test_from_review_captures_state_before_the_card_is_mutated() -> None:
    word = make_word(box=LeitnerBox.FAMILIAR, due_at=NOW - timedelta(days=3))
    word.progress.last_reviewed_at = NOW - timedelta(days=7)

    event = ReviewEvent.from_review(word, ReviewGrade.GOOD, LeitnerBox.KNOWN, NOW)
    word.progress.apply_review(ReviewGrade.GOOD, LeitnerBox.KNOWN, NOW + timedelta(days=9), NOW)

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
