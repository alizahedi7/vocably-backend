"""Linking a phone and an email to one account, and taking either off again.

The claim under test is the one in the feature's name: whichever identifier an
account was created with, signing in with the other one afterwards opens the
*same* account — and no sequence of links and unlinks leaves an account with no
way in.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_email_otp_sender
from app.application.ports.email_otp_sender import EmailOTPSender
from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.infrastructure.db.models.link_challenge import LinkChallengeModel
from app.infrastructure.db.models.user import UserModel
from app.main import app
from tests.api.conftest import (
    RecordingEmailOTPSender,
    RecordingOTPSender,
    UserFactory,
    bearer,
)

LINK = "/api/v1/auth/link"
EMAIL = "ali@example.com"
OTHER_PHONE = "+989120000002"
Headers = dict[str, str]


async def ask(client: AsyncClient, headers: Headers, kind: str, target: str) -> Response:
    return await client.post(
        f"{LINK}/request-otp", json={"type": kind, "target": target}, headers=headers
    )


async def confirm(
    client: AsyncClient, headers: Headers, kind: str, target: str, code: str
) -> Response:
    return await client.post(
        f"{LINK}/verify-otp",
        json={"type": kind, "target": target, "code": code},
        headers=headers,
    )


async def link_email(
    client: AsyncClient, headers: Headers, sender: RecordingEmailOTPSender, email: str = EMAIL
) -> Response:
    sent = await ask(client, headers, "email", email)
    assert sent.status_code == 202, sent.text
    return await confirm(client, headers, "email", email, sender.last_code_for(email))


def wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


# ── The two directions ───────────────────────────────────────


async def test_a_phone_account_links_an_email_and_google_then_opens_the_same_account(
    client: AsyncClient,
    user: UserModel,
    auth_headers: Headers,
    email_sender: RecordingEmailOTPSender,
) -> None:
    sent = await ask(client, auth_headers, "email", EMAIL)
    assert sent.status_code == 202, sent.text
    assert sent.json() == {
        "detail": "Verification code sent.",
        "type": "email",
        "target": EMAIL,
        "expires_in": settings.link_otp_ttl_seconds,
        "resend_after": 0,  # the suite runs with the cooldown off
    }

    linked = await confirm(client, auth_headers, "email", EMAIL, email_sender.last_code_for(EMAIL))
    assert linked.status_code == 200, linked.text
    body = linked.json()
    assert body["id"] == str(user.id)
    assert (body["phone"], body["is_phone_verified"]) == (user.phone, True)
    assert (body["email"], body["is_email_verified"]) == (EMAIL, True)
    # How the account was made is history; linking does not rewrite it.
    assert body["auth_method"] == "phone"

    google = await client.post("/api/v1/auth/google", json={"id_token": f"g1:{EMAIL}:Ali"})
    assert google.status_code == 200, google.text
    assert google.json()["is_new_user"] is False
    assert google.json()["user"]["id"] == str(user.id)


async def test_a_google_account_links_a_phone_and_the_phone_then_opens_the_same_account(
    client: AsyncClient, otp_sender: RecordingOTPSender
) -> None:
    created = await client.post("/api/v1/auth/google", json={"id_token": f"g1:{EMAIL}:Ali"})
    account = created.json()
    headers = {"Authorization": f"Bearer {account['tokens']['access_token']}"}

    sent = await ask(client, headers, "phone", OTHER_PHONE)
    assert sent.status_code == 202, sent.text
    linked = await confirm(
        client, headers, "phone", OTHER_PHONE, otp_sender.last_code_for(OTHER_PHONE)
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["phone"] == OTHER_PHONE
    assert linked.json()["auth_method"] == "google"

    await client.post("/api/v1/auth/otp/request", json={"phone": OTHER_PHONE})
    signed_in = await client.post(
        "/api/v1/auth/otp/verify",
        json={"phone": OTHER_PHONE, "code": otp_sender.last_code_for(OTHER_PHONE)},
    )
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["is_new_user"] is False
    assert signed_in.json()["user"]["id"] == account["user"]["id"]


async def test_the_profile_says_what_is_verified(
    client: AsyncClient, auth_headers: Headers
) -> None:
    me = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
    assert me["is_phone_verified"] is True
    assert (me["email"], me["is_email_verified"]) == (None, False)


# ── Input ────────────────────────────────────────────────────


async def test_the_target_is_normalised_once_and_matched_however_it_is_typed(
    client: AsyncClient,
    auth_headers: Headers,
    email_sender: RecordingEmailOTPSender,
    otp_sender: RecordingOTPSender,
    make_user: UserFactory,
) -> None:
    sent = await ask(client, auth_headers, "email", "  Ali@Example.COM ")
    assert sent.json()["target"] == EMAIL
    linked = await confirm(
        client, auth_headers, "email", "ALI@example.com", email_sender.last_code_for(EMAIL)
    )
    assert linked.json()["email"] == EMAIL

    other = await make_user(phone=None, google_sub="g-9", auth_method="google", name="Sara")
    sent = await ask(client, bearer(other.id), "phone", "+98 (912) 000-0002")
    assert sent.json()["target"] == OTHER_PHONE
    linked = await confirm(
        client,
        bearer(other.id),
        "phone",
        "0098 912 000 0002",
        otp_sender.last_code_for(OTHER_PHONE),
    )
    assert linked.json()["phone"] == OTHER_PHONE


@pytest.mark.parametrize(
    ("kind", "target"),
    [
        ("email", "not-an-email"),
        ("email", "ali@example"),
        ("phone", "09121234567"),  # no country code, and none is guessed
        ("phone", "ali@example.com"),
    ],
)
async def test_a_malformed_target_is_refused_with_copy_and_sends_nothing(
    client: AsyncClient,
    auth_headers: Headers,
    email_sender: RecordingEmailOTPSender,
    otp_sender: RecordingOTPSender,
    kind: str,
    target: str,
) -> None:
    response = await ask(client, auth_headers, kind, target)
    assert response.status_code == 422
    # The app renders ``detail`` verbatim, so it has to be a sentence.
    assert response.json()["error"]["code"] == "validation_error"
    assert isinstance(response.json()["detail"], str)
    assert email_sender.outbox == otp_sender.outbox == []


async def test_an_unknown_kind_is_refused(client: AsyncClient, auth_headers: Headers) -> None:
    assert (await ask(client, auth_headers, "passport", "x")).status_code == 422
    assert (await client.delete(f"{LINK}/passport", headers=auth_headers)).status_code == 422


async def test_every_linking_route_needs_a_session(client: AsyncClient) -> None:
    payload = {"type": "email", "target": EMAIL, "code": "123456"}
    assert (await client.post(f"{LINK}/request-otp", json=payload)).status_code == 401
    assert (await client.post(f"{LINK}/verify-otp", json=payload)).status_code == 401
    assert (await client.delete(f"{LINK}/email")).status_code == 401


# ── Somebody else's, and your own ────────────────────────────


async def test_a_phone_another_account_holds_is_refused_and_no_code_is_sent(
    client: AsyncClient,
    auth_headers: Headers,
    make_user: UserFactory,
    otp_sender: RecordingOTPSender,
) -> None:
    await make_user(phone=OTHER_PHONE, name="Sara")

    response = await ask(client, auth_headers, "phone", OTHER_PHONE)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "identifier_already_in_use"
    # There is no merge; the copy has to say what does work instead.
    assert "remove it there" in response.json()["detail"]
    assert otp_sender.outbox == []


async def test_only_a_verified_email_counts_as_in_use(
    client: AsyncClient,
    auth_headers: Headers,
    make_user: UserFactory,
    email_sender: RecordingEmailOTPSender,
) -> None:
    await make_user(
        phone=None, google_sub="g-1", auth_method="google", email=EMAIL, is_email_verified=True
    )
    taken = await ask(client, auth_headers, "email", EMAIL)
    assert taken.status_code == 409
    assert taken.json()["error"]["code"] == "identifier_already_in_use"

    # An address a Google token merely claimed belongs to nobody yet. If it
    # blocked linking, anyone could squat on an inbox they do not control.
    claimed = "claimed@corp.example"
    await make_user(phone=None, google_sub="g-2", auth_method="google", email=claimed)
    linked = await link_email(client, auth_headers, email_sender, claimed)
    assert linked.status_code == 200, linked.text
    assert linked.json()["is_email_verified"] is True


async def test_asking_for_what_is_already_yours_is_refused_without_a_code(
    client: AsyncClient,
    user: UserModel,
    auth_headers: Headers,
    otp_sender: RecordingOTPSender,
) -> None:
    assert user.phone is not None
    response = await ask(client, auth_headers, "phone", user.phone)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert otp_sender.outbox == []


async def test_the_unique_index_decides_when_two_accounts_prove_one_email(
    client: AsyncClient,
    auth_headers: Headers,
    make_user: UserFactory,
    email_sender: RecordingEmailOTPSender,
) -> None:
    sara = await make_user(phone=OTHER_PHONE, name="Sara")
    # Both ask while nobody holds it, so both are sent a code.
    assert (await ask(client, auth_headers, "email", EMAIL)).status_code == 202
    ali_code = email_sender.last_code_for(EMAIL)
    assert (await ask(client, bearer(sara.id), "email", EMAIL)).status_code == 202
    sara_code = email_sender.last_code_for(EMAIL)

    assert (await confirm(client, auth_headers, "email", EMAIL, ali_code)).status_code == 200

    late = await confirm(client, bearer(sara.id), "email", EMAIL, sara_code)
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "identifier_already_in_use"
    me = (await client.get("/api/v1/users/me", headers=bearer(sara.id))).json()
    assert me["email"] is None


# ── The code ─────────────────────────────────────────────────


async def test_a_wrong_code_is_a_bad_request_not_a_dead_session(
    client: AsyncClient, auth_headers: Headers, email_sender: RecordingEmailOTPSender
) -> None:
    await ask(client, auth_headers, "email", EMAIL)
    code = email_sender.last_code_for(EMAIL)

    response = await confirm(client, auth_headers, "email", EMAIL, wrong(code))

    # 400, never 401: a client that sees 401 on a route carrying a bearer token
    # refreshes, retries — a second attempt spent — and then signs the user out.
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_otp"
    assert "2 tries left" in response.json()["detail"]


async def test_the_code_dies_after_three_wrong_guesses(
    client: AsyncClient, auth_headers: Headers, email_sender: RecordingEmailOTPSender
) -> None:
    await ask(client, auth_headers, "email", EMAIL)
    code = email_sender.last_code_for(EMAIL)

    for _ in range(settings.link_otp_max_attempts):
        assert (await confirm(client, auth_headers, "email", EMAIL, wrong(code))).status_code == 400

    # Exhausted: the attempts were committed although each request failed, so
    # even the right code no longer links.
    response = await confirm(client, auth_headers, "email", EMAIL, code)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_otp"
    me = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
    assert me["email"] is None


async def test_an_expired_code_is_refused(
    client: AsyncClient,
    auth_headers: Headers,
    email_sender: RecordingEmailOTPSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "link_otp_ttl_seconds", 0)
    await ask(client, auth_headers, "email", EMAIL)

    response = await confirm(
        client, auth_headers, "email", EMAIL, email_sender.last_code_for(EMAIL)
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_otp"


async def test_a_code_proves_only_the_address_it_was_sent_to(
    client: AsyncClient, auth_headers: Headers, email_sender: RecordingEmailOTPSender
) -> None:
    await ask(client, auth_headers, "email", EMAIL)
    code = email_sender.last_code_for(EMAIL)

    # The right code, presented for an address it was never sent to.
    swapped = await confirm(client, auth_headers, "email", "victim@example.com", code)
    assert swapped.status_code == 400
    # Nor does it cross kinds.
    crossed = await confirm(client, auth_headers, "phone", OTHER_PHONE, code)
    assert crossed.status_code == 400

    me = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
    assert me["email"] is None


async def test_a_code_is_single_use(
    client: AsyncClient, auth_headers: Headers, email_sender: RecordingEmailOTPSender
) -> None:
    assert (await link_email(client, auth_headers, email_sender)).status_code == 200
    code = email_sender.last_code_for(EMAIL)
    assert (await client.delete(f"{LINK}/email", headers=auth_headers)).status_code == 200

    replay = await confirm(client, auth_headers, "email", EMAIL, code)
    assert replay.status_code == 400


async def test_a_second_tap_on_verify_is_not_an_error(
    client: AsyncClient, auth_headers: Headers, email_sender: RecordingEmailOTPSender
) -> None:
    assert (await link_email(client, auth_headers, email_sender)).status_code == 200
    again = await confirm(client, auth_headers, "email", EMAIL, email_sender.last_code_for(EMAIL))
    assert again.status_code == 200
    assert again.json()["email"] == EMAIL


async def test_asking_again_retires_the_previous_code(
    client: AsyncClient, auth_headers: Headers, email_sender: RecordingEmailOTPSender
) -> None:
    # Distinct codes, so "the first no longer works" is not vacuous.
    codes = iter(["111111", "222222"])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "app.application.services.account_link_service.generate_otp", lambda: next(codes)
        )
        await ask(client, auth_headers, "email", EMAIL)
        await ask(client, auth_headers, "email", EMAIL)

    assert (await confirm(client, auth_headers, "email", EMAIL, "111111")).status_code == 400
    assert (await confirm(client, auth_headers, "email", EMAIL, "222222")).status_code == 200


async def test_a_delivery_failure_leaves_no_code_behind(
    client: AsyncClient,
    auth_headers: Headers,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class FailingEmailSender(EmailOTPSender):
        async def send(self, email: str, code: str) -> None:
            raise ExternalServiceError("Failed to send verification email.")

    app.dependency_overrides[get_email_otp_sender] = lambda: FailingEmailSender()

    response = await ask(client, auth_headers, "email", EMAIL)

    assert response.status_code == 502
    async with session_factory() as session:
        stored = await session.scalar(select(func.count()).select_from(LinkChallengeModel))
    assert stored == 0


# ── Rate limits ──────────────────────────────────────────────


async def test_a_second_code_inside_the_cooldown_is_refused(
    client: AsyncClient,
    auth_headers: Headers,
    email_sender: RecordingEmailOTPSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "link_otp_cooldown_seconds", 60)

    first = await ask(client, auth_headers, "email", EMAIL)
    assert first.status_code == 202
    assert first.json()["resend_after"] == 60

    retry = await ask(client, auth_headers, "email", EMAIL)
    assert retry.status_code == 429
    assert retry.json()["error"]["code"] == "rate_limited"
    # The refused resend must not have retired the code already sent.
    linked = await confirm(client, auth_headers, "email", EMAIL, email_sender.last_code_for(EMAIL))
    assert linked.status_code == 200

    # Per kind: waiting on an email code does not hold up adding a phone.
    assert (await ask(client, auth_headers, "phone", OTHER_PHONE)).status_code == 202


async def test_codes_are_capped_per_hour(
    client: AsyncClient, auth_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "link_otp_requests_per_hour", 2)

    for _ in range(2):
        assert (await ask(client, auth_headers, "email", EMAIL)).status_code == 202
    blocked = await ask(client, auth_headers, "email", EMAIL)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


async def test_asking_whether_a_number_is_taken_costs_a_slot(
    client: AsyncClient,
    auth_headers: Headers,
    make_user: UserFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order is the control: throttle first, *then* say "in use".

    A 409 tells the caller that a number has an account. If refusals were free,
    the endpoint would answer that question for any number, as often as asked.
    """
    monkeypatch.setattr(settings, "link_otp_requests_per_hour", 1)
    await make_user(phone=OTHER_PHONE, name="Sara")
    await make_user(phone="+989120000003", name="Reza")

    assert (await ask(client, auth_headers, "phone", OTHER_PHONE)).status_code == 409
    # The budget is spent, so the next probe learns nothing.
    assert (await ask(client, auth_headers, "phone", "+989120000003")).status_code == 429


async def test_one_address_is_capped_across_accounts(
    client: AsyncClient,
    auth_headers: Headers,
    make_user: UserFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "link_otp_requests_per_target_per_hour", 1)
    sara = await make_user(phone=OTHER_PHONE, name="Sara")
    # Unique per run: this key is the address itself, and a developer's Redis
    # remembers it for an hour.
    target = f"{uuid4().hex}@example.com"

    assert (await ask(client, auth_headers, "email", target)).status_code == 202
    assert (await ask(client, bearer(sara.id), "email", target)).status_code == 429


# ── Unlinking, and never locking anyone out ──────────────────


async def test_a_phone_cannot_be_removed_until_google_has_actually_signed_in(
    client: AsyncClient,
    user: UserModel,
    auth_headers: Headers,
    email_sender: RecordingEmailOTPSender,
) -> None:
    alone = await client.delete(f"{LINK}/phone", headers=auth_headers)
    assert alone.status_code == 409
    assert alone.json()["error"]["code"] == "last_sign_in_method"

    # A verified email is not yet a way in: nothing signs in with it until
    # Google has, and this address might not be a Google account at all.
    assert (await link_email(client, auth_headers, email_sender)).status_code == 200
    still = await client.delete(f"{LINK}/phone", headers=auth_headers)
    assert still.status_code == 409

    google = await client.post("/api/v1/auth/google", json={"id_token": f"g1:{EMAIL}:Ali"})
    assert google.json()["user"]["id"] == str(user.id)

    removed = await client.delete(f"{LINK}/phone", headers=auth_headers)
    assert removed.status_code == 200, removed.text
    assert (removed.json()["phone"], removed.json()["is_phone_verified"]) == (None, False)

    # And now the email is the last way in.
    last = await client.delete(f"{LINK}/email", headers=auth_headers)
    assert last.status_code == 409
    assert last.json()["error"]["code"] == "last_sign_in_method"


async def test_removing_the_email_disconnects_google_too(
    client: AsyncClient, otp_sender: RecordingOTPSender
) -> None:
    token = f"g1:{EMAIL}:Ali"
    account = (await client.post("/api/v1/auth/google", json={"id_token": token})).json()
    headers = {"Authorization": f"Bearer {account['tokens']['access_token']}"}

    # Google is the only way in, so the email stays.
    assert (await client.delete(f"{LINK}/email", headers=headers)).status_code == 409

    await ask(client, headers, "phone", OTHER_PHONE)
    await confirm(client, headers, "phone", OTHER_PHONE, otp_sender.last_code_for(OTHER_PHONE))
    removed = await client.delete(f"{LINK}/email", headers=headers)
    assert removed.status_code == 200, removed.text
    assert (removed.json()["email"], removed.json()["is_email_verified"]) == (None, False)

    # The Google account no longer opens this one — that is what removing it means.
    again = (await client.post("/api/v1/auth/google", json={"id_token": token})).json()
    assert again["is_new_user"] is True
    assert again["user"]["id"] != account["user"]["id"]


async def test_removing_what_is_not_there_changes_nothing(
    client: AsyncClient, user: UserModel, auth_headers: Headers
) -> None:
    response = await client.delete(f"{LINK}/email", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["phone"] == user.phone
    assert response.json()["email"] is None


async def test_changing_the_email_keeps_google_sign_in(
    client: AsyncClient, email_sender: RecordingEmailOTPSender
) -> None:
    """Replacing an address must not remove a way in.

    A Google-only account that changes its email still has exactly one way to
    sign in — the Google identity it had. Clearing that with the old address
    would lock the account the moment the new one turned out not to be a
    Google account.
    """
    token = f"g1:{EMAIL}:Ali"
    account = (await client.post("/api/v1/auth/google", json={"id_token": token})).json()
    headers = {"Authorization": f"Bearer {account['tokens']['access_token']}"}

    changed = await link_email(client, headers, email_sender, "new@elsewhere.example")
    assert changed.status_code == 200, changed.text
    assert changed.json()["email"] == "new@elsewhere.example"

    again = (await client.post("/api/v1/auth/google", json={"id_token": token})).json()
    assert again["user"]["id"] == account["user"]["id"]
    # And the address the owner proved is not overwritten by the one Google sent.
    assert again["user"]["email"] == "new@elsewhere.example"


async def test_an_unverified_google_email_can_be_proven_with_a_code(
    client: AsyncClient,
    make_user: UserFactory,
    email_sender: RecordingEmailOTPSender,
) -> None:
    claimed = "ali@corp.example"
    account = await make_user(
        phone=None, google_sub="google-g7", auth_method="google", email=claimed
    )

    linked = await link_email(client, bearer(account.id), email_sender, claimed)
    assert linked.status_code == 200, linked.text
    assert linked.json()["is_email_verified"] is True

    # Still the same account through Google, which never vouched for it.
    google = await client.post(
        "/api/v1/auth/google", json={"id_token": f"g7:{claimed}:Ali:unverified"}
    )
    assert google.json()["user"]["id"] == str(account.id)
    assert google.json()["user"]["is_email_verified"] is True
