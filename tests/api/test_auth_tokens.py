"""Bearer-token edge cases: expiry, deleted users, malformed subjects."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token
from app.infrastructure.db.models.user import UserModel


async def test_expired_access_token_is_rejected(
    client: AsyncClient, user: UserModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "access_token_expire_minutes", -1)
    token = create_access_token(user.id)

    response = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_token_for_deleted_user_is_rejected(client: AsyncClient) -> None:
    token = create_access_token(uuid4())

    response = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_token_with_malformed_subject_is_rejected(client: AsyncClient) -> None:
    token = create_access_token("not-a-uuid")

    response = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_refresh_token_cannot_be_used_as_access_token(
    client: AsyncClient, user: UserModel
) -> None:
    token = create_refresh_token(user.id)

    response = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
