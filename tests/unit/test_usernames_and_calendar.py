"""Handle rules and the one place a day boundary is decided — pure, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.services import usernames
from app.domain.services.calendar import (
    day_bounds,
    is_valid_timezone,
    today_for,
    week_bounds,
    week_start_for,
    zone_for,
)


# ── handles ──────────────────────────────────────────────────
@pytest.mark.parametrize("handle", ["ali", "ali_z", "a1b2c3", "x" * 20])
def test_well_formed_handles_are_accepted(handle: str) -> None:
    assert usernames.is_valid_username(handle)


@pytest.mark.parametrize(
    "handle",
    [
        "ab",  # too short
        "x" * 21,  # too long
        "1ali",  # must start with a letter
        "_ali",
        "Ali",  # stored form is lowercase
        "ali z",
        "ali-z",
        "ali.z",
        "",
    ],
)
def test_malformed_handles_are_rejected(handle: str) -> None:
    assert not usernames.is_valid_username(handle)


@pytest.mark.parametrize("handle", ["admin", "vocably", "support", "me", "api", "join", "null"])
def test_reserved_handles_are_invalid_not_merely_taken(handle: str) -> None:
    # Invalid, so the answer is the same whether or not anyone holds them —
    # the endpoint cannot be used to probe which reserved names exist. `/join/`
    # and `/users/me` are real paths.
    assert not usernames.is_valid_username(handle)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ali Zahedi", "alizahedi"),
        ("  Sara  ", "sara"),
        ("Ali-Reza", "alireza"),
        ("123Ali", "ali"),  # leading non-letters are trimmed, not rejected
        ("علی", ""),  # nothing usable survives; the caller decides
        ("Jo", ""),  # too short after slugifying
        ("A" * 40, "a" * 20),
    ],
)
def test_slugify_matches_the_clients_rules(name: str, expected: str) -> None:
    assert usernames.slugify(name) == expected


def test_fallback_is_valid_and_deterministic() -> None:
    user_id = UUID("1a2b3c4d-0000-0000-0000-000000000000")
    handle = usernames.fallback_username(user_id)

    assert handle == "user_1a2b3c4d"
    assert usernames.is_valid_username(handle)
    # Deterministic, so re-running a backfill proposes the same handle rather
    # than a second one.
    assert usernames.fallback_username(user_id) == handle


def test_normalize_folds_to_the_stored_form() -> None:
    assert usernames.normalize("  ALI_Z  ") == "ali_z"


# ── day and week boundaries ──────────────────────────────────
def test_a_day_boundary_is_local_not_utc() -> None:
    # 00:30 UTC is already "tomorrow" in Tehran (+03:30). Computing the day in
    # UTC is what silently broke a streak the learner had kept.
    just_after_midnight_utc = datetime(2026, 8, 6, 0, 30, tzinfo=UTC)

    assert today_for("UTC", just_after_midnight_utc).isoformat() == "2026-08-06"
    assert today_for("Asia/Tehran", just_after_midnight_utc).isoformat() == "2026-08-06"

    # ...and 23:00 UTC is already tomorrow there.
    late = datetime(2026, 8, 6, 23, 0, tzinfo=UTC)
    assert today_for("UTC", late).isoformat() == "2026-08-06"
    assert today_for("Asia/Tehran", late).isoformat() == "2026-08-07"


def test_weeks_start_on_monday_like_the_client() -> None:
    # 2026-08-06 is a Thursday.
    thursday = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    assert week_start_for("UTC", thursday).isoformat() == "2026-08-03"  # the Monday

    monday = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    assert week_start_for("UTC", monday).isoformat() == "2026-08-03"  # itself


def test_day_bounds_are_utc_instants_covering_the_local_day() -> None:
    day = today_for("Asia/Tehran", datetime(2026, 8, 6, 12, 0, tzinfo=UTC))
    start, end = day_bounds(day, "Asia/Tehran")

    assert start.tzinfo is UTC and end.tzinfo is UTC
    assert start < end
    assert (end - start).total_seconds() == 86_400
    # Tehran is +03:30, so its midnight is 20:30 the previous UTC day.
    assert start.isoformat() == "2026-08-05T20:30:00+00:00"


def test_week_bounds_span_monday_to_sunday_inclusive() -> None:
    day = today_for("UTC", datetime(2026, 8, 6, 12, 0, tzinfo=UTC))
    start, end = week_bounds(day, "UTC")

    assert start.isoformat() == "2026-08-03T00:00:00+00:00"
    assert end.isoformat() == "2026-08-10T00:00:00+00:00"  # half-open


def test_an_unknown_timezone_falls_back_rather_than_raising() -> None:
    # A bad name reaches here from a client that shipped one; a home screen that
    # 500s is worse than a boundary in the wrong place. The *write* rejects it.
    assert zone_for("Mars/Olympus") == zone_for(None)
    assert today_for("Mars/Olympus") == today_for("UTC")
    assert not is_valid_timezone("Mars/Olympus")
    assert is_valid_timezone("Asia/Tehran")
