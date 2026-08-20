"""Moving a card to a Leitner box because the learner said so.

A mastered card does not come back for three weeks, so a learner who knows
perfectly well they have forgotten one has no way to say so through the review
loop — its whole answer is "we will ask you in twenty-one days".
``POST /study/words/{id}/box`` is that override.

**Most of what these tests pin is a negative.** The tempting implementation is
``POST /grade`` with ``again``: it lands on box 1 and the call already exists.
It is also wrong, because grading writes a review event, awards XP, records
deck activity, notes the study day and settles the streak. A learner tidying
fifteen mastered words would earn ninety XP for reviewing nothing, could bank
the daily goal without answering a card, and would publish fifteen reviews that
never happened to everyone else in a shared deck. So the tests below check the
box moved *and* that none of those five things happened.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.daily_deck_activity import DailyDeckActivityModel
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.word_progress import WordProgressModel
from app.infrastructure.db.models.word_review import WordReviewModel
from tests.api.conftest import UserFactory, bearer, sleep_on_it


async def create_deck(client: AsyncClient, headers: dict[str, str], name: str = "Deck") -> str:
    response = await client.post("/api/v1/decks", headers=headers, json={"name": name, "hue": 262})
    assert response.status_code == 201, response.text
    deck_id: str = response.json()["id"]
    return deck_id


async def add_word(client: AsyncClient, headers: dict[str, str], deck_id: str, term: str) -> str:
    response = await client.post(
        "/api/v1/words",
        headers=headers,
        json={"deck_id": deck_id, "term": term, "meaning": f"meaning of {term}"},
    )
    assert response.status_code == 201, response.text
    word_id: str = response.json()["id"]
    return word_id


async def set_box(
    client: AsyncClient, headers: dict[str, str], word_id: str, box: int
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/study/words/{word_id}/box", headers=headers, json={"box": box}
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def grade(client: AsyncClient, headers: dict[str, str], word_id: str, value: str) -> None:
    response = await client.post(
        f"/api/v1/study/words/{word_id}/grade", headers=headers, json={"grade": value}
    )
    assert response.status_code == 200, response.text


async def climb_to_mastered(
    client: AsyncClient,
    headers: dict[str, str],
    word_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Answer a card up to box 5 the honest way, a day at a time."""
    for _ in range(4):
        await sleep_on_it(session_factory)
        await grade(client, headers, word_id, "good")


async def xp_of(client: AsyncClient, headers: dict[str, str]) -> int:
    body = (await client.get("/api/v1/users/me", headers=headers)).json()
    value: int = body["xp"]
    return value


async def reviews_logged(
    session_factory: async_sessionmaker[AsyncSession], word_id: str
) -> list[WordReviewModel]:
    async with session_factory() as session:
        stmt = select(WordReviewModel).where(WordReviewModel.word_id == UUID(word_id))
        return list((await session.execute(stmt)).scalars().all())


async def activity_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[DailyDeckActivityModel]:
    async with session_factory() as session:
        return list((await session.execute(select(DailyDeckActivityModel))).scalars().all())


async def progress_row(
    session_factory: async_sessionmaker[AsyncSession], word_id: str
) -> WordProgressModel | None:
    async with session_factory() as session:
        stmt = select(WordProgressModel).where(WordProgressModel.word_id == UUID(word_id))
        return (await session.execute(stmt)).scalars().first()


# ── it moves the card ────────────────────────────────────────
async def test_a_mastered_word_can_be_sent_back_to_new(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "undermine")
    await climb_to_mastered(client, auth_headers, word, session_factory)
    assert (await client.get("/api/v1/words", headers=auth_headers)).json()[0]["box"] == 5

    body = await set_box(client, auth_headers, word, 1)

    assert body["box"] == 1
    # The response is the card as it now stands, so the client can apply it
    # without a second read.
    listed = (await client.get("/api/v1/words", headers=auth_headers)).json()[0]
    assert listed["box"] == 1


async def test_any_box_is_reachable_both_ways(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The endpoint takes a target box, not a bare "reset".

    That is what makes the client's undo the same call with the box the card
    came from; a one-way ``/reset`` would need a second endpoint to undo it.
    """
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "reluctant")
    await climb_to_mastered(client, auth_headers, word, session_factory)

    assert (await set_box(client, auth_headers, word, 1))["box"] == 1
    assert (await set_box(client, auth_headers, word, 5))["box"] == 5
    assert (await set_box(client, auth_headers, word, 3))["box"] == 3


# ── and does none of the five things a grade does ────────────
async def test_moving_a_card_is_not_a_review(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The test this endpoint exists for.

    Every assertion here fails if ``set_box`` is implemented as a grade.
    """
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "undermine")
    await climb_to_mastered(client, auth_headers, word, session_factory)

    xp_before = await xp_of(client, auth_headers)
    reviews_before = len(await reviews_logged(session_factory, word))
    overview_before = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    row_before = await progress_row(session_factory, word)
    assert row_before is not None
    counters_before = (
        row_before.review_count,
        row_before.lapse_count,
        row_before.consecutive_correct,
        row_before.last_reviewed_at,
        row_before.last_grade,
        row_before.first_reviewed_at,
    )
    activity_before = {
        (a.user_id, a.deck_id, a.day): a.reviews for a in await activity_rows(session_factory)
    }

    await set_box(client, auth_headers, word, 1)

    # 1. No XP. A learner tidying their mastered box has not studied.
    assert await xp_of(client, auth_headers) == xp_before
    # 2. No review event. The log is what "they answered this card" means.
    assert len(await reviews_logged(session_factory, word)) == reviews_before
    # 3. No deck activity, which is what a shared deck's roster publishes to
    #    everyone else as "Active today · N reviews".
    activity_after = {
        (a.user_id, a.deck_id, a.day): a.reviews for a in await activity_rows(session_factory)
    }
    assert activity_after == activity_before
    # 4. The day and the streak are untouched.
    overview_after = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert overview_after["reviewed_today"] == overview_before["reviewed_today"]
    assert overview_after["streak"] == overview_before["streak"]
    assert overview_after["day_state"] == overview_before["day_state"]
    # 5. And the row's own review history survives the move — the counters say
    #    what this learner did with the card, and they did not do anything.
    row_after = await progress_row(session_factory, word)
    assert row_after is not None
    assert (
        row_after.review_count,
        row_after.lapse_count,
        row_after.consecutive_correct,
        row_after.last_reviewed_at,
        row_after.last_grade,
        row_after.first_reviewed_at,
    ) == counters_before


async def test_moving_a_card_cannot_bank_the_daily_goal(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The sharpest edge of the same rule.

    A goal of one and a card moved: if the move counted, the learner would have
    banked the day and advanced their streak without answering anything.
    """
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"daily_goal": 1})
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "improve")
    await sleep_on_it(session_factory)

    await set_box(client, auth_headers, word, 1)

    overview = (await client.get("/api/v1/study/overview", headers=auth_headers)).json()
    assert overview["streak"] == 0
    assert overview["day_state"] != "banked"
    claim = await client.post("/api/v1/study/day/celebration", headers=auth_headers)
    assert claim.json()["status"] == "unbanked"


async def test_mastered_at_is_not_stamped_by_a_hand_set_box(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``mastered_at - first_reviewed_at`` measures how long a card took to
    learn. A card put in box 5 by hand did not learn it."""
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "borrow")
    await sleep_on_it(session_factory)
    await grade(client, auth_headers, word, "good")

    await set_box(client, auth_headers, word, 5)

    row = await progress_row(session_factory, word)
    assert row is not None
    assert row.box == 5
    assert row.mastered_at is None


# ── the schedule it lands on ─────────────────────────────────
async def test_the_card_comes_back_tomorrow_not_in_this_minute(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Box 1's own interval, from the start of the learner's day.

    The learner has just said they do not know the word; asking again thirty
    seconds later measures nothing, which is the same reason a newly added word
    is tomorrow's work.
    """
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "undermine")
    await climb_to_mastered(client, auth_headers, word, session_factory)

    await set_box(client, auth_headers, word, 1)

    assert (await client.get("/api/v1/study/overview", headers=auth_headers)).json()[
        "due_count"
    ] == 0
    row = await progress_row(session_factory, word)
    assert row is not None
    assert row.due_at > datetime.now(UTC)

    await sleep_on_it(session_factory)
    assert (await client.get("/api/v1/study/overview", headers=auth_headers)).json()[
        "due_count"
    ] == 1


async def test_moving_back_up_restores_the_boxs_own_schedule(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """What makes the client's undo an undo.

    Restoring the box and leaving the card due tomorrow would put a mastered
    word back in tomorrow's queue — the thing the learner had just taken back.
    """
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "reluctant")
    await climb_to_mastered(client, auth_headers, word, session_factory)

    await set_box(client, auth_headers, word, 1)
    await set_box(client, auth_headers, word, 5)

    row = await progress_row(session_factory, word)
    assert row is not None
    # Box 5's interval is 21 days; anything near tomorrow means the schedule
    # was not restored with the box.
    assert row.due_at > datetime.now(UTC) + timedelta(days=20)


# ── who may call it, and on what ─────────────────────────────
async def test_a_card_nobody_has_started_cannot_be_moved(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Putting a card into the boxes is ``POST /decks/{id}/start``.

    Letting a scheduling endpoint do it would smuggle a start through the back
    door, and the learner would find a word in their queue they never took on.
    """
    author = await make_user(phone="+989120000001")
    source = await create_deck(client, bearer(author.id), "504 Essential Words")
    await add_word(client, bearer(author.id), source, "alpha")
    async with session_factory() as session:
        await session.execute(
            update(DeckModel)
            .where(DeckModel.id == UUID(source))
            .values(
                is_public=True,
                category="general",
                description="A deck",
                description_fa="یک دسته",
                published_at=datetime.now(UTC),
            )
        )
        await session.commit()
    imported = await client.post(f"/api/v1/decks/public/{source}/import", headers=auth_headers)
    copy_id = imported.json()["id"]
    word = (await client.get(f"/api/v1/words?deck_id={copy_id}", headers=auth_headers)).json()[0]
    assert word["started"] is False

    refused = await client.post(
        f"/api/v1/study/words/{word['id']}/box", headers=auth_headers, json={"box": 1}
    )

    assert refused.status_code == 409, refused.text
    # And nothing was written: a refusal must not leave the card half-started.
    assert await progress_row(session_factory, word["id"]) is None


async def test_a_stranger_cannot_move_a_card_they_cannot_see(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
) -> None:
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "improve")
    stranger = await make_user(phone="+989120000002")

    refused = await client.post(
        f"/api/v1/study/words/{word}/box", headers=bearer(stranger.id), json={"box": 1}
    )

    # 404 rather than 403, for the reason grading answers 404: a 403 would
    # confirm that another class's card exists.
    assert refused.status_code == 404, refused.text


async def test_a_viewer_may_move_their_own_boxes(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Study is not an edit.

    What this writes is the caller's own queue, which nobody else can see, so a
    viewer of a class deck may move their boxes exactly as they may grade.
    """
    student = await make_user(phone="+989120000003", username="student")
    deck = await create_deck(client, auth_headers, "Class deck")
    word = await add_word(client, auth_headers, deck, "improve")
    added = await client.post(
        f"/api/v1/decks/{deck}/members",
        headers=auth_headers,
        json={"username": "student", "role": "viewer"},
    )
    assert added.status_code in (200, 201), added.text
    # A deck that arrived from somebody else is self-paced, so the student's
    # copy of the card is not in their boxes until they take it on. Starting it
    # is their own call to make — `require_read` is enough, since what it writes
    # is their queue.
    started = await client.post(
        f"/api/v1/decks/{deck}/start", headers=bearer(student.id), json={"word_ids": [word]}
    )
    assert started.status_code in (200, 201), started.text

    body = await set_box(client, bearer(student.id), word, 3)

    assert body["box"] == 3
    # The owner's own boxes are untouched: one card, two learners, two rows.
    owner_word = (await client.get("/api/v1/words", headers=auth_headers)).json()[0]
    assert owner_word["box"] == 1


async def test_an_unknown_word_is_404(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    from uuid import uuid4

    response = await client.post(
        f"/api/v1/study/words/{uuid4()}/box", headers=auth_headers, json={"box": 1}
    )
    assert response.status_code == 404, response.text


async def test_a_box_outside_the_ladder_is_rejected(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "improve")

    for box in (0, 6, 99, -1):
        response = await client.post(
            f"/api/v1/study/words/{word}/box", headers=auth_headers, json={"box": box}
        )
        assert response.status_code == 422, f"box={box}: {response.text}"


# ── a card with no progress row yet ──────────────────────────
async def test_a_never_graded_card_in_an_ordinary_deck_can_be_moved(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ "Started" and "has a progress row" are different questions.

    A card in a deck the learner built is in their boxes from the moment it is
    created, but nothing has written a row for it — the rows are lazy. The write
    is an upsert precisely so this case creates one rather than failing.
    """
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "improve")
    assert await progress_row(session_factory, word) is None

    body = await set_box(client, auth_headers, word, 4)

    assert body["box"] == 4
    row = await progress_row(session_factory, word)
    assert row is not None
    # Created by the move, and carrying no review history — because there is
    # none.
    assert row.review_count == 0
    assert row.last_reviewed_at is None
    assert row.first_reviewed_at is None
    assert row.last_grade is None


async def test_the_day_the_move_happened_is_not_recorded_as_studied(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``users.last_studied_on`` drives the streak. A move is not studying."""
    deck = await create_deck(client, auth_headers)
    word = await add_word(client, auth_headers, deck, "improve")

    await set_box(client, auth_headers, word, 2)

    me = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
    assert me["streak"] == 0
    assert not isinstance(me.get("last_studied_on"), date)
