"""Review history: the event log written on every grade, and the card counters.

These run against the real request→persist path, so they cover the mappers and
the repository as well as the service.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import LeitnerBox, ReviewGrade
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word_progress import WordProgressModel
from app.infrastructure.db.models.word_review import WordReviewModel
from app.infrastructure.db.repositories.review_event_repository import (
    SqlAlchemyReviewEventRepository,
)
from tests.api.conftest import UserFactory, bearer
from tests.api.test_study import seed_deck_with_words


async def grade(
    client: AsyncClient, headers: dict[str, str], word_id: str, value: str, **extra: object
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/study/words/{word_id}/grade",
        headers=headers,
        json={"grade": value, **extra},
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def fetch_events(
    session_factory: async_sessionmaker[AsyncSession], word_id: str
) -> list[WordReviewModel]:
    async with session_factory() as session:
        stmt = (
            select(WordReviewModel)
            .where(WordReviewModel.word_id == UUID(word_id))
            .order_by(WordReviewModel.id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def test_every_grade_appends_one_immutable_event(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])

    for value in ("good", "easy", "again"):
        await grade(client, auth_headers, word_id, value)

    events = await fetch_events(session_factory, word_id)
    assert len(events) == 3  # appended, never overwritten
    assert [e.grade for e in events] == [
        ReviewGrade.GOOD.ordinal,
        ReviewGrade.EASY.ordinal,
        ReviewGrade.AGAIN.ordinal,
    ]
    # The box trajectory the card row can no longer tell us: 1→2→4→1.
    assert [(e.box_before, e.box_after) for e in events] == [(1, 2), (2, 4), (4, 1)]


async def test_event_records_the_scheduling_context_of_the_review(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])

    await grade(client, auth_headers, word_id, "good")
    await grade(client, auth_headers, word_id, "good")
    first, second = await fetch_events(session_factory, word_id)

    assert first.elapsed_seconds is None  # a card's first review has no interval
    assert second.elapsed_seconds is not None and second.elapsed_seconds >= 0
    # The card was just scheduled 2 days out, so the second review is early.
    assert second.overdue_seconds is not None and second.overdue_seconds < 0


async def test_optional_client_fields_are_recorded_and_clamped(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])
    session = str(uuid4())

    await grade(client, auth_headers, word_id, "good", latency_ms=4200, session_id=session)
    (event,) = await fetch_events(session_factory, word_id)
    assert event.latency_ms == 4200
    assert event.session_id == UUID(session)

    # Out-of-range latency is rejected at the schema boundary rather than stored.
    rejected = await client.post(
        f"/api/v1/study/words/{word_id}/grade",
        headers=auth_headers,
        json={"grade": "good", "latency_ms": 99_999_999},
    )
    assert rejected.status_code == 422


async def test_grading_without_the_optional_fields_still_works(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Clients that predate latency_ms/session_id must keep working untouched.
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])
    await grade(client, auth_headers, word_id, "good")

    (event,) = await fetch_events(session_factory, word_id)
    assert event.latency_ms is None
    assert event.session_id is None


async def test_counters_on_the_progress_row_track_difficulty_and_mastery(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: UserModel,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])

    for value in ("again", "good", "again", "easy", "easy"):
        await grade(client, auth_headers, word_id, value)

    # The counters live on the learner's progress row, not the card: the card
    # is shared, and two members' counters against it are different numbers.
    async with session_factory() as session:
        progress = await session.get(WordProgressModel, (user.id, UUID(word_id)))
    assert progress is not None
    assert progress.review_count == 5
    assert progress.lapse_count == 2
    assert progress.consecutive_correct == 2  # the run since the last `again`
    assert progress.last_grade == ReviewGrade.EASY.ordinal
    assert progress.first_reviewed_at is not None
    assert progress.mastered_at is not None  # reached box 5
    assert progress.box == LeitnerBox.MASTERED


async def test_no_progress_row_exists_until_the_card_is_reviewed(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: UserModel,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])

    # Not "a row of zeroes" but *no row at all*: progress is created lazily, and
    # an unreviewed card reads as box 1 / due now without one existing.
    async with session_factory() as session:
        progress = await session.get(WordProgressModel, (user.id, UUID(word_id)))
    assert progress is None

    overview = await client.get("/api/v1/study/overview", headers=auth_headers)
    assert overview.json()["due_count"] == 1


async def test_rejected_grades_write_no_event(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])
    other = await make_user(phone="+989121110000")

    denied = await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=bearer(other.id), json={"grade": "good"}
    )
    # 404, not 403: someone who is in none of the card's decks must not be able
    # to learn that the card exists by probing its id.
    assert denied.status_code == 404
    assert await fetch_events(session_factory, word_id) == []


async def test_deleting_a_card_removes_its_history(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # ON DELETE CASCADE: "delete this card" must mean it, for GDPR erasure and
    # for the learner's own expectation of what deleting a card does.
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])
    await grade(client, auth_headers, word_id, "good")
    assert len(await fetch_events(session_factory, word_id)) == 1

    deleted = await client.delete(f"/api/v1/words/{word_id}", headers=auth_headers)
    assert deleted.status_code in (200, 204)
    assert await fetch_events(session_factory, word_id) == []


async def test_repository_returns_a_cards_history_newest_first(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, (word_id,) = await seed_deck_with_words(client, auth_headers, ["improve"])
    for value in ("good", "again", "easy"):
        await grade(client, auth_headers, word_id, value)

    async with session_factory() as session:
        events = await SqlAlchemyReviewEventRepository(session).list_for_word(UUID(word_id))

    assert [e.grade for e in events] == [ReviewGrade.EASY, ReviewGrade.AGAIN, ReviewGrade.GOOD]
    assert all(e.word_id == UUID(word_id) for e in events)
    # Enums are decoded back out of their stored integer form.
    assert events[0].box_before is LeitnerBox.NEW
    assert events[-1].box_after is LeitnerBox.LEARNING


async def test_history_is_scoped_per_card(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, (first_id, second_id) = await seed_deck_with_words(client, auth_headers, ["a", "b"])
    await grade(client, auth_headers, first_id, "good")
    await grade(client, auth_headers, first_id, "again")
    await grade(client, auth_headers, second_id, "easy")

    assert len(await fetch_events(session_factory, first_id)) == 2
    assert len(await fetch_events(session_factory, second_id)) == 1
