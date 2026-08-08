"""A saved deck's words wait to be started.

The problem these tests pin down: progress rows are lazy, and a *missing* row
has always read as "box 1, due now". Save "504 Essential Words" from Explore and
five hundred cards land in one day's review queue — which is the fastest way to
make someone close the app. So a membership that arrived from elsewhere (an
Explore copy, a share, an invite code) is **self-paced**: a card with no
progress row is *not started* there, and the learner adds cards a unit, a batch
or a card at a time.

What must not change is the deck someone builds themselves. Adding a card is
already the act of deciding to learn it, and a second "start" step on your own
word would be exactly the friction this feature removes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.deck import DeckModel
from tests.api.conftest import UserFactory, bearer, sleep_on_it


async def create_deck(client: AsyncClient, headers: dict[str, str], name: str = "Deck") -> str:
    response = await client.post("/api/v1/decks", headers=headers, json={"name": name, "hue": 262})
    assert response.status_code == 201, response.text
    deck_id: str = response.json()["id"]
    return deck_id


async def add_word(
    client: AsyncClient,
    headers: dict[str, str],
    deck_id: str,
    term: str,
    *,
    unit_id: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "deck_id": deck_id,
        "term": term,
        "meaning": f"meaning of {term}",
    }
    if unit_id is not None:
        payload["unit_id"] = unit_id
    response = await client.post("/api/v1/words", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    word_id: str = response.json()["id"]
    return word_id


async def publish(session_factory: async_sessionmaker[AsyncSession], deck_id: str) -> None:
    """Publishing is admin-only, so tests set the flag directly."""
    async with session_factory() as session:
        await session.execute(
            update(DeckModel)
            .where(DeckModel.id == UUID(deck_id))
            .values(
                is_public=True,
                category="general",
                description="A deck",
                description_fa="یک دسته",
                published_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def saved_copy(
    client: AsyncClient,
    author_headers: dict[str, str],
    learner_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    *,
    terms: tuple[str, ...] = ("alpha", "beta", "gamma"),
) -> str:
    """A published deck, saved by the learner. Returns *their* copy's id."""
    source = await create_deck(client, author_headers, "504 Essential Words")
    for term in terms:
        await add_word(client, author_headers, source, term)
    await publish(session_factory, source)
    imported = await client.post(f"/api/v1/decks/public/{source}/import", headers=learner_headers)
    assert imported.status_code in (200, 201), imported.text
    copy_id: str = imported.json()["id"]
    return copy_id


def deck_row(payload: list[dict[str, object]], deck_id: str) -> dict[str, object]:
    return next(d for d in payload if d["id"] == deck_id)


# ── a deck you built yourself is untouched ───────────────────
async def test_your_own_deck_still_puts_new_words_straight_into_the_boxes(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    deck = await create_deck(client, auth_headers, "Mine")
    await add_word(client, auth_headers, deck, "improve")

    words = (await client.get("/api/v1/words", headers=auth_headers)).json()
    assert [w["started"] for w in words] == [True]

    decks = (await client.get("/api/v1/decks", headers=auth_headers)).json()
    mine = deck_row(decks, deck)
    assert mine["self_paced"] is False
    assert mine["word_count"] == mine["started_count"] == 1
    # In the boxes the moment it is written — and, like any new word, first
    # reviewed tomorrow rather than the same minute.
    assert mine["due_count"] == 0
    await sleep_on_it(session_factory)
    assert (
        deck_row((await client.get("/api/v1/decks", headers=auth_headers)).json(), deck)[
            "due_count"
        ]
        == 1
    )


# ── a saved deck waits ───────────────────────────────────────
async def test_saving_from_explore_leaves_every_word_unstarted(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    learner = await make_user(phone="+989120000101", name="Learner")
    copy = await saved_copy(client, auth_headers, bearer(learner.id), session_factory)

    words = (
        await client.get("/api/v1/words", headers=bearer(learner.id), params={"deck_id": copy})
    ).json()
    assert len(words) == 3
    assert [w["started"] for w in words] == [False, False, False]
    # The placeholder values an older client renders the row with are still
    # there — nothing about the wire shape moved.
    assert all(w["box"] == 1 for w in words)

    decks = (await client.get("/api/v1/decks", headers=bearer(learner.id))).json()
    row = deck_row(decks, copy)
    assert row["self_paced"] is True
    # The deck is still 3 words big; none of them is the learner's yet.
    assert row["word_count"] == 3
    assert row["started_count"] == 0
    assert row["due_count"] == 0

    # Nothing is due, and nothing to review — the whole point.
    overview = (await client.get("/api/v1/study/overview", headers=bearer(learner.id))).json()
    assert overview["due_count"] == 0
    assert overview["total_count"] == 0
    session = (await client.get("/api/v1/study/session", headers=bearer(learner.id))).json()
    assert session["words"] == []


async def test_an_accepted_share_waits_too_while_the_owner_is_unaffected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    student = await make_user(phone="+989120000102", name="Student")
    await client.patch("/api/v1/users/me", headers=bearer(student.id), json={"username": "student"})
    deck = await create_deck(client, auth_headers, "Class list")
    await add_word(client, auth_headers, deck, "alpha")

    shared = await client.post(
        f"/api/v1/decks/{deck}/share", headers=auth_headers, json={"to_username": "student"}
    )
    assert shared.status_code in (200, 201), shared.text
    offers = (await client.get("/api/v1/decks/shared", headers=bearer(student.id))).json()
    offer_id = offers["decks"][0]["id"]
    accepted = await client.post(
        f"/api/v1/decks/shared/{offer_id}/accept", headers=bearer(student.id)
    )
    assert accepted.status_code in (200, 201), accepted.text

    student_decks = (await client.get("/api/v1/decks", headers=bearer(student.id))).json()
    row = deck_row(student_decks, deck)
    assert row["self_paced"] is True
    assert row["word_count"] == 1
    assert row["started_count"] == 0

    # The teacher's own deck is theirs; it was never self-paced, and once its
    # word has had its night it is in their queue.
    await sleep_on_it(session_factory)
    owner_row = deck_row((await client.get("/api/v1/decks", headers=auth_headers)).json(), deck)
    assert owner_row["self_paced"] is False
    assert owner_row["due_count"] == 1
    # The student, who has started nothing, still has nothing waiting.
    student_row = deck_row(
        (await client.get("/api/v1/decks", headers=bearer(student.id))).json(), deck
    )
    assert student_row["due_count"] == 0


# ── starting them ────────────────────────────────────────────
async def test_starting_a_batch_takes_the_next_words_in_order(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    learner = await make_user(phone="+989120000103", name="Learner")
    headers = bearer(learner.id)
    copy = await saved_copy(client, auth_headers, headers, session_factory)

    started = await client.post(f"/api/v1/decks/{copy}/start", headers=headers, json={"count": 2})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["started"] == 2
    assert body["remaining"] == 1
    assert len(body["started_ids"]) == 2

    words = (await client.get("/api/v1/words", headers=headers, params={"deck_id": copy})).json()
    # The deck's own order — the book's, preserved through the copy. The word
    # list comes back newest-first, so reversing it is the sequence the deck
    # was authored in, and "the next two" are its first two.
    authored = list(reversed(words))
    assert [w["started"] for w in authored] == [True, True, False]
    assert set(body["started_ids"]) == {authored[0]["id"], authored[1]["id"]}

    row = deck_row((await client.get("/api/v1/decks", headers=headers)).json(), copy)
    assert row["started_count"] == 2
    assert row["word_count"] == 3
    # Started today, so first reviewed tomorrow — starting a word is meeting
    # it, and the interval starts from there.
    assert row["due_count"] == 0

    same_day = (await client.get("/api/v1/study/overview", headers=headers)).json()
    assert same_day["total_count"] == 2
    assert same_day["due_count"] == 0

    await sleep_on_it(session_factory)
    overview = (await client.get("/api/v1/study/overview", headers=headers)).json()
    assert overview["due_count"] == 2
    assert overview["total_count"] == 2


async def test_starting_one_unit_starts_only_that_unit(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    source = await create_deck(client, auth_headers, "American English File 1")
    unit_one = (
        await client.post(
            f"/api/v1/decks/{source}/units", headers=auth_headers, json={"name": "Unit 1"}
        )
    ).json()["id"]
    unit_two = (
        await client.post(
            f"/api/v1/decks/{source}/units", headers=auth_headers, json={"name": "Unit 2"}
        )
    ).json()["id"]
    await add_word(client, auth_headers, source, "one", unit_id=unit_one)
    await add_word(client, auth_headers, source, "two", unit_id=unit_two)
    await add_word(client, auth_headers, source, "loose")
    await publish(session_factory, source)

    learner = await make_user(phone="+989120000104", name="Learner")
    headers = bearer(learner.id)
    copy = (await client.post(f"/api/v1/decks/public/{source}/import", headers=headers)).json()[
        "id"
    ]
    words = (await client.get("/api/v1/words", headers=headers, params={"deck_id": copy})).json()
    copied_unit_one = next(w["unit_id"] for w in words if w["term"] == "one")

    started = await client.post(
        f"/api/v1/decks/{copy}/start", headers=headers, json={"unit_id": copied_unit_one}
    )
    assert started.json()["started"] == 1
    assert started.json()["remaining"] == 2

    after = (await client.get("/api/v1/words", headers=headers, params={"deck_id": copy})).json()
    assert {w["term"]: w["started"] for w in after} == {
        "one": True,
        "two": False,
        "loose": False,
    }


async def test_starting_specific_words_is_idempotent_and_never_resets_a_box(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    learner = await make_user(phone="+989120000105", name="Learner")
    headers = bearer(learner.id)
    copy = await saved_copy(client, auth_headers, headers, session_factory)
    words = (await client.get("/api/v1/words", headers=headers, params={"deck_id": copy})).json()
    first = words[0]["id"]

    assert (
        await client.post(
            f"/api/v1/decks/{copy}/start", headers=headers, json={"word_ids": [first]}
        )
    ).json()["started"] == 1
    graded = await client.post(
        f"/api/v1/study/words/{first}/grade", headers=headers, json={"grade": "good"}
    )
    assert graded.status_code == 200, graded.text
    assert graded.json()["box"] == 2

    # A second tap adds nothing and, crucially, does not send the card back to
    # box 1 — starting is joining the queue, not restarting it.
    again = await client.post(
        f"/api/v1/decks/{copy}/start", headers=headers, json={"word_ids": [first]}
    )
    assert again.json()["started"] == 0
    still = (await client.get(f"/api/v1/words/{first}", headers=headers)).json()
    assert still["box"] == 2
    assert still["started"] is True


async def test_starting_with_no_selector_takes_the_whole_deck(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    learner = await make_user(phone="+989120000106", name="Learner")
    headers = bearer(learner.id)
    copy = await saved_copy(client, auth_headers, headers, session_factory)

    body = (await client.post(f"/api/v1/decks/{copy}/start", headers=headers, json={})).json()
    assert body["started"] == 3
    assert body["remaining"] == 0
    row = deck_row((await client.get("/api/v1/decks", headers=headers)).json(), copy)
    assert row["started_count"] == row["word_count"] == 3


# ── undoing a start ──────────────────────────────────────────
async def test_undo_removes_untouched_words_and_spares_reviewed_ones(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    learner = await make_user(phone="+989120000107", name="Learner")
    headers = bearer(learner.id)
    copy = await saved_copy(client, auth_headers, headers, session_factory)
    started = (await client.post(f"/api/v1/decks/{copy}/start", headers=headers, json={})).json()
    ids = started["started_ids"]

    # One of them has since been answered. An undo must not erase that.
    await client.post(
        f"/api/v1/study/words/{ids[0]}/grade", headers=headers, json={"grade": "good"}
    )

    undone = await client.post(
        f"/api/v1/decks/{copy}/unstart", headers=headers, json={"word_ids": ids}
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["started"] == -2
    assert undone.json()["remaining"] == 2

    words = (await client.get("/api/v1/words", headers=headers, params={"deck_id": copy})).json()
    kept = next(w for w in words if w["id"] == ids[0])
    assert kept["started"] is True
    assert kept["box"] == 2
    assert sum(1 for w in words if w["started"]) == 1


# ── adding to a self-paced deck ──────────────────────────────
async def test_a_word_you_add_yourself_is_started_even_in_a_saved_deck(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    learner = await make_user(phone="+989120000108", name="Learner")
    headers = bearer(learner.id)
    copy = await saved_copy(client, auth_headers, headers, session_factory)

    mine = await add_word(client, headers, copy, "typed-by-me")
    word = (await client.get(f"/api/v1/words/{mine}", headers=headers)).json()
    assert word["started"] is True

    # And it alone: saving the deck did not start the rest.
    row = deck_row((await client.get("/api/v1/decks", headers=headers)).json(), copy)
    assert row["started_count"] == 1
    assert row["word_count"] == 4


# ── access ───────────────────────────────────────────────────
async def test_a_stranger_cannot_start_words_in_a_deck_they_cannot_see(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
) -> None:
    deck = await create_deck(client, auth_headers, "Private")
    await add_word(client, auth_headers, deck, "secret")
    stranger = await make_user(phone="+989120000109", name="Stranger")

    refused = await client.post(f"/api/v1/decks/{deck}/start", headers=bearer(stranger.id), json={})
    # 404, not 403: a deck id they are not in must not be confirmed to exist.
    assert refused.status_code == 404
