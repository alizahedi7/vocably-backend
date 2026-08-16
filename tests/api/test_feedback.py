"""Feedback: written reports, and thumbs on AI card backs.

The two halves are tested against opposite standards, which is the design (see
``FeedbackService``). A report is allowed to fail loudly and must never be lost;
a rating must never fail at all except where accepting it would corrupt the data.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.infrastructure.db.models.feedback import AIFeedbackModel, FeedbackReportModel
from app.infrastructure.db.models.user import UserModel
from tests.api.conftest import UserFactory, bearer

REPORT_URL = "/api/v1/feedback/report"
RATE_URL = "/api/v1/ai/feedback"


async def _lookup(
    client: AsyncClient, headers: dict[str, str], term: str = "run"
) -> dict[str, Any]:
    response = await client.post("/api/v1/ai/lookup", headers=headers, json={"term": term})
    assert response.status_code == 200, response.text
    return response.json()


# ── the written report ───────────────────────────────────────
async def test_report_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(REPORT_URL, json={"kind": "bug", "message": "It crashed."})
    assert response.status_code == 401


async def test_report_is_stored_with_its_client_metadata(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: UserModel,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await client.post(
        REPORT_URL,
        headers=auth_headers,
        json={
            "kind": "bug",
            "message": "The study screen freezes after the third card.",
            "app_version": "1.5.0+11",
            "platform": "android",
            "os_version": "Android 14 (SDK 34)",
            "locale": "fa",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "bug"
    # The id is the one thing the client did not already have, and the one thing
    # a learner can quote when following up.
    assert body["id"]

    async with session_factory() as session:
        stored = (await session.execute(select(FeedbackReportModel))).scalars().one()
    assert stored.user_id == user.id
    assert stored.message == "The study screen freezes after the third card."
    assert stored.app_version == "1.5.0+11"
    assert stored.platform == "android"
    assert stored.os_version == "Android 14 (SDK 34)"
    assert stored.locale == "fa"


async def test_report_survives_a_client_that_cannot_describe_itself(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Metadata is never a reason to refuse a report.

    An unknown platform is stored as ``unknown`` and an over-long OS string is
    truncated — neither costs the learner the paragraph they just wrote.
    """
    response = await client.post(
        REPORT_URL,
        headers=auth_headers,
        json={
            "kind": "wat",
            "message": "Something is off with the streak count.",
            "platform": "symbian",
            "os_version": "x" * 199,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "other"

    async with session_factory() as session:
        stored = (await session.execute(select(FeedbackReportModel))).scalars().one()
    assert stored.platform == "unknown"
    assert len(stored.os_version) == 120


@pytest.mark.parametrize("message", ["", "  ", "hi"])
async def test_report_needs_something_to_read(
    client: AsyncClient, auth_headers: dict[str, str], message: str
) -> None:
    response = await client.post(REPORT_URL, headers=auth_headers, json={"message": message})
    assert response.status_code == 422


async def test_report_is_rate_limited_per_user(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one endpoint that writes unbounded free text on request."""
    monkeypatch.setattr(settings, "feedback_reports_per_user_per_hour", 2)
    for _ in range(2):
        ok = await client.post(
            REPORT_URL, headers=auth_headers, json={"message": "Something is broken."}
        )
        assert ok.status_code == 201, ok.text
    blocked = await client.post(
        REPORT_URL, headers=auth_headers, json={"message": "Something is broken."}
    )
    assert blocked.status_code == 429
    # The client renders `detail` verbatim — see ApiClient._extractDetail.
    assert blocked.json()["detail"]


async def test_deleting_the_account_keeps_the_report_and_drops_the_link(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: UserModel,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A bug does not stop existing because the reporter left.

    ``ON DELETE SET NULL``, not ``CASCADE``: the account's link to the report
    goes, the sentence stays.
    """
    await client.post(REPORT_URL, headers=auth_headers, json={"message": "Deck search is slow."})
    async with session_factory() as session:
        await session.delete(await session.get(UserModel, user.id))
        await session.commit()
        stored = (await session.execute(select(FeedbackReportModel))).scalars().one()
        assert stored.user_id is None
        assert stored.message == "Deck search is slow."


# ── the AI rating ────────────────────────────────────────────
async def test_lookup_hands_back_an_id_to_rate_it_by(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = await _lookup(client, auth_headers)
    assert body["lookup_id"]
    # Deterministic, so two learners rating the same word aggregate into one
    # score rather than two.
    again = await _lookup(client, auth_headers)
    assert again["lookup_id"] == body["lookup_id"]


async def test_rating_is_stored_with_the_provenance_of_what_it_rates(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    lookup = await _lookup(client, auth_headers)
    response = await client.post(
        RATE_URL,
        headers=auth_headers,
        json={"lookup_id": lookup["lookup_id"], "sense_index": 1, "rating": "up"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rating"] == "up"

    async with session_factory() as session:
        stored = (await session.execute(select(AIFeedbackModel))).scalars().one()
    assert stored.lookup_id == lookup["lookup_id"]
    assert stored.sense_index == 1
    assert stored.rating == "up"
    # Denormalised from the cache entry, so the row still says which word once
    # that entry has been swept.
    assert stored.term == "run"
    assert stored.prompt_version > 0


async def test_changing_your_mind_moves_the_row_rather_than_adding_one(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The identity is (user, lookup, sense) — which is also what makes the
    endpoint safe for a client to fire and forget."""
    lookup = await _lookup(client, auth_headers)
    payload = {"lookup_id": lookup["lookup_id"], "sense_index": 0}
    await client.post(RATE_URL, headers=auth_headers, json={**payload, "rating": "up"})
    await client.post(RATE_URL, headers=auth_headers, json={**payload, "rating": "down"})

    async with session_factory() as session:
        rows = (await session.execute(select(AIFeedbackModel))).scalars().all()
    assert len(rows) == 1
    assert rows[0].rating == "down"


async def test_tapping_the_lit_thumb_again_withdraws_the_verdict(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A withdrawn rating is deleted, never stored as an opinion nobody holds."""
    lookup = await _lookup(client, auth_headers)
    payload = {"lookup_id": lookup["lookup_id"], "sense_index": 0}
    await client.post(RATE_URL, headers=auth_headers, json={**payload, "rating": "up"})
    withdrawn = await client.post(
        RATE_URL, headers=auth_headers, json={**payload, "rating": "none"}
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["rating"] == "none"

    async with session_factory() as session:
        assert (await session.execute(select(AIFeedbackModel))).scalars().all() == []


async def test_a_reason_is_kept_only_under_a_thumbs_down(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A reason beside a positive verdict would read as "the example was bad —
    and I liked it", so it is dropped rather than stored."""
    lookup = await _lookup(client, auth_headers)
    payload = {"lookup_id": lookup["lookup_id"], "sense_index": 0}
    down = await client.post(
        RATE_URL, headers=auth_headers, json={**payload, "rating": "down", "reason": "bad_example"}
    )
    assert down.json()["reason"] == "bad_example"

    up = await client.post(
        RATE_URL, headers=auth_headers, json={**payload, "rating": "up", "reason": "bad_example"}
    )
    assert up.json()["reason"] is None
    async with session_factory() as session:
        stored = (await session.execute(select(AIFeedbackModel))).scalars().one()
    assert stored.reason is None


async def test_an_unreadable_reason_still_stores_the_rating(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The thumb is the signal; the chip is a bonus. Losing the second must
    never lose the first."""
    lookup = await _lookup(client, auth_headers)
    response = await client.post(
        RATE_URL,
        headers=auth_headers,
        json={
            "lookup_id": lookup["lookup_id"],
            "sense_index": 0,
            "rating": "down",
            "reason": "the_vibes",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["rating"] == "down"
    assert response.json()["reason"] is None


async def test_a_sense_the_deck_never_had_is_refused(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """One of the only two rejections left: it would corrupt the score rather
    than inconvenience anybody."""
    lookup = await _lookup(client, auth_headers)
    response = await client.post(
        RATE_URL,
        headers=auth_headers,
        json={"lookup_id": lookup["lookup_id"], "sense_index": 40, "rating": "down"},
    )
    assert response.status_code == 422


async def test_an_unknown_lookup_is_accepted_rather_than_refused(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An entry can be missing for reasons the learner cannot see — a swept
    prompt version, an input too long to cache — and there is nowhere on their
    screen to explain a refusal."""
    response = await client.post(
        RATE_URL,
        headers=auth_headers,
        json={"lookup_id": "f" * 64, "sense_index": 0, "rating": "down"},
    )
    assert response.status_code == 200, response.text
    async with session_factory() as session:
        stored = (await session.execute(select(AIFeedbackModel))).scalars().one()
    # Nothing could be resolved, so nothing is claimed.
    assert stored.term == ""
    # …but the deployment's own configuration still stamps what it can.
    assert stored.prompt_version > 0


async def test_two_learners_rating_the_same_sense_both_count(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    other = await make_user(phone="+989120000002")
    lookup = await _lookup(client, auth_headers)
    payload = {"lookup_id": lookup["lookup_id"], "sense_index": 0, "rating": "down"}
    await client.post(RATE_URL, headers=auth_headers, json=payload)
    await client.post(RATE_URL, headers=bearer(other.id), json=payload)

    async with session_factory() as session:
        rows = (await session.execute(select(AIFeedbackModel))).scalars().all()
    assert len(rows) == 2


async def test_deleting_the_account_anonymises_the_rating(
    client: AsyncClient,
    auth_headers: dict[str, str],
    user: UserModel,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A rating is not personal data — its value is entirely in (lookup, sense,
    rating) — so deletion anonymises it rather than destroying the signal."""
    lookup = await _lookup(client, auth_headers)
    await client.post(
        RATE_URL,
        headers=auth_headers,
        json={"lookup_id": lookup["lookup_id"], "sense_index": 0, "rating": "down"},
    )
    async with session_factory() as session:
        await session.delete(await session.get(UserModel, user.id))
        await session.commit()
        stored = (await session.execute(select(AIFeedbackModel))).scalars().one()
    assert stored.user_id is None
    assert stored.rating == "down"


# ── the admin read surface ───────────────────────────────────
async def test_admin_surfaces_are_closed_to_ordinary_users(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    for url in ("/api/v1/admin/feedback", "/api/v1/admin/ai-feedback"):
        assert (await client.get(url, headers=auth_headers)).status_code == 403
        assert (await client.get(url)).status_code == 401


async def test_admin_reads_reports_newest_first(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    admin = await make_user(phone="+989120000003", is_admin=True)
    await client.post(REPORT_URL, headers=auth_headers, json={"kind": "bug", "message": "First."})
    await client.post(REPORT_URL, headers=auth_headers, json={"kind": "idea", "message": "Second."})

    response = await client.get("/api/v1/admin/feedback", headers=bearer(admin.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [item["message"] for item in body["items"]] == ["Second.", "First."]
    # camelCase, to match the vocably-admin TypeScript types.
    assert "createdAt" in body["items"][0]
    assert "appVersion" in body["items"][0]

    filtered = await client.get("/api/v1/admin/feedback?kind=idea", headers=bearer(admin.id))
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["message"] == "Second."


async def test_admin_scores_ai_senses_worst_first(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: UserFactory,
) -> None:
    admin = await make_user(phone="+989120000004", is_admin=True)
    lookup = await _lookup(client, auth_headers)
    lookup_id = lookup["lookup_id"]
    # Sense 0 liked; sense 1 disliked, with a reason.
    await client.post(
        RATE_URL,
        headers=auth_headers,
        json={"lookup_id": lookup_id, "sense_index": 0, "rating": "up"},
    )
    await client.post(
        RATE_URL,
        headers=auth_headers,
        json={
            "lookup_id": lookup_id,
            "sense_index": 1,
            "rating": "down",
            "reason": "wrong_meaning",
        },
    )

    response = await client.get("/api/v1/admin/ai-feedback", headers=bearer(admin.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["ups"], body["downs"]) == (1, 1)
    # Per sense, not per lookup: a deck whose second card is wrong and whose
    # first is fine is exactly what averaging would hide.
    assert len(body["items"]) == 2
    worst = body["items"][0]
    assert worst["senseIndex"] == 1
    assert worst["downs"] == 1
    assert worst["wrongMeaning"] == 1
    assert worst["term"] == "run"


async def test_rating_is_rate_limited_per_user(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ceiling on a stuck client, not a budget the learner can feel."""
    monkeypatch.setattr(settings, "ai_feedback_per_user_per_hour", 1)
    lookup = await _lookup(client, auth_headers)
    payload = {"lookup_id": lookup["lookup_id"], "sense_index": 0, "rating": "up"}
    assert (await client.post(RATE_URL, headers=auth_headers, json=payload)).status_code == 200
    assert (await client.post(RATE_URL, headers=auth_headers, json=payload)).status_code == 429


async def test_rating_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(
        RATE_URL, json={"lookup_id": "x", "sense_index": 0, "rating": "up"}
    )
    assert response.status_code == 401
