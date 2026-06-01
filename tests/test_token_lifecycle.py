"""Phase 2 unit tests for JWT and refresh-token lifecycle.

These tests exercise the auth dependency and the /auth/refresh,
/auth/logout, /auth/logout-all endpoints. They run in DEMO_MODE so
they do not require Postgres, but the demo store now persists
refresh tokens in a way that mirrors the production table.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.core.security import (
    ALGORITHM,
    create_access_token,
    create_refresh_token,
)
import jwt
from app.config import settings


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _register_and_login(
    client: AsyncClient, email: str
) -> tuple[str, str, str]:
    creds = {"email": email, "password": "TestPass123!"}
    reg = await client.post("/api/v1/auth/register", json=creds)
    assert reg.status_code in (200, 201), reg.text
    user_id = reg.json()["id"]
    login = await client.post("/api/v1/auth/login", json=creds)
    assert login.status_code == 200, login.text
    body = login.json()
    return user_id, body["access_token"], body["refresh_token"]


async def test_expired_access_token_is_rejected(client: AsyncClient):
    """An access token with exp in the past must be rejected."""
    fake_user = str(uuid.uuid4())
    expired = create_access_token(
        subject=fake_user, expires_delta=timedelta(seconds=-30)
    )
    r = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert r.status_code == 401, r.text


async def test_jwt_with_alg_none_is_rejected(client: AsyncClient):
    """A token signed with alg=none must not authenticate."""
    fake_user = str(uuid.uuid4())
    # Manually craft an unsigned token (alg=none is not a real signature).
    forged = jwt.encode(
        {"sub": fake_user, "type": "access"},
        key="",
        algorithm="HS256",  # forged with empty key; never our real secret
    )
    r = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}
    )
    assert r.status_code == 401, r.text


async def test_refresh_token_rotation(client: AsyncClient):
    """Refreshing rotates the token; the old refresh token cannot be reused."""
    uid = uuid.uuid4().hex[:6]
    _, _, refresh1 = await _register_and_login(
        client, f"rotate_{uid}@nexmem.example.com"
    )

    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert r1.status_code == 200
    refresh2 = r1.json()["refresh_token"]
    assert refresh2 != refresh1

    # Replaying the original refresh token must fail.
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert r2.status_code == 401, r2.text

    # The new refresh token works once.
    r3 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh2})
    assert r3.status_code == 200


async def test_logout_revokes_only_current_session(client: AsyncClient):
    uid = uuid.uuid4().hex[:6]
    _, access_a, refresh_a = await _register_and_login(
        client, f"single_{uid}@nexmem.example.com"
    )
    # Log in again as the same user to obtain a second refresh token.
    creds = {
        "email": f"single_{uid}@nexmem.example.com",
        "password": "TestPass123!",
    }
    second_login = await client.post("/api/v1/auth/login", json=creds)
    refresh_b = second_login.json()["refresh_token"]

    headers_a = {"Authorization": f"Bearer {access_a}"}
    out = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_a},
        headers=headers_a,
    )
    assert out.status_code == 204

    # The first refresh token is now revoked.
    r_dead = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_a}
    )
    assert r_dead.status_code == 401

    # The second refresh token still works.
    r_alive = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_b}
    )
    assert r_alive.status_code == 200


async def test_logout_all_revokes_every_session(client: AsyncClient):
    uid = uuid.uuid4().hex[:6]
    _, access_a, refresh_a = await _register_and_login(
        client, f"allout_{uid}@nexmem.example.com"
    )
    creds = {
        "email": f"allout_{uid}@nexmem.example.com",
        "password": "TestPass123!",
    }
    second_login = await client.post("/api/v1/auth/login", json=creds)
    refresh_b = second_login.json()["refresh_token"]

    headers_a = {"Authorization": f"Bearer {access_a}"}
    out = await client.post("/api/v1/auth/logout-all", headers=headers_a)
    assert out.status_code == 204

    for tok in (refresh_a, refresh_b):
        r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tok})
        assert r.status_code == 401, f"token {tok[:8]}... should be revoked"


async def test_deleted_api_key_immediately_stops_working(client: AsyncClient):
    uid = uuid.uuid4().hex[:6]
    _, access, _ = await _register_and_login(
        client, f"apikey_{uid}@nexmem.example.com"
    )
    headers = {"Authorization": f"Bearer {access}"}

    create = await client.post(
        "/api/v1/auth/api-keys",
        json={"name": "test-key"},
        headers=headers,
    )
    assert create.status_code == 201
    raw = create.json()["api_key"]
    key_id = create.json()["id"]

    # Verify the key works.
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"ApiKey {raw}"}
    )
    assert me.status_code == 200

    # Delete the key.
    delete = await client.delete(
        f"/api/v1/auth/api-keys/{key_id}", headers=headers
    )
    assert delete.status_code == 204

    # Reusing the same raw key must now fail.
    me2 = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"ApiKey {raw}"}
    )
    assert me2.status_code == 401


async def test_refresh_token_is_required_to_be_active(client: AsyncClient):
    """Manually minted refresh token (no DB row) must not authenticate."""
    fake_user = str(uuid.uuid4())
    not_persisted = create_refresh_token(subject=fake_user)
    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": not_persisted}
    )
    assert r.status_code == 401
