"""Refresh-token revocation: live-DB integration tests (R-H11).

Covers:
  1. Login persists a refresh_tokens row.
  2. /auth/refresh rotates the row (revokes old, creates new).
  3. The old (rotated-away) token cannot mint a fresh access token.
  4. /auth/logout revokes the supplied token; replay returns 401.
  5. /auth/logout-all revokes every active refresh token for the user;
     none of them can mint a new access token afterwards.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text


pytestmark = pytest.mark.asyncio


async def _login(http_client: AsyncClient) -> dict:
    email = f"rt_{uuid.uuid4().hex[:8]}@example.com"
    password = "RotateMe!2026"
    await http_client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    login = await http_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    body = login.json()
    me = await http_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    return {
        "email": email,
        "password": password,
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "user_id": me.json()["id"],
        "headers": {"Authorization": f"Bearer {body['access_token']}"},
    }


# 1. Login persists a row.


async def test_login_persists_refresh_token_row(http_client) -> None:
    user = await _login(http_client)
    from app.database import async_session

    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": user["user_id"]},
        )
        r = await s.execute(text("SELECT count(*) FROM refresh_tokens WHERE user_id = :u"), {"u": user["user_id"]})
        assert (r.scalar() or 0) == 1


# 2 & 3. Rotation revokes the old token; old token cannot mint again.


async def test_refresh_rotates_and_old_token_cannot_be_replayed(http_client) -> None:
    user = await _login(http_client)
    old = user["refresh_token"]

    rotate = await http_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old}
    )
    assert rotate.status_code == 200, rotate.text
    new = rotate.json()["refresh_token"]
    assert new != old

    # Replay the OLD token: must fail with 401.
    replay = await http_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old}
    )
    assert replay.status_code == 401

    # NEW token works once.
    rotate2 = await http_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new}
    )
    assert rotate2.status_code == 200


# 4. /auth/logout revokes the token.


async def test_logout_revokes_specific_refresh_token(http_client) -> None:
    user = await _login(http_client)
    out = await http_client.post(
        "/api/v1/auth/logout", json={"refresh_token": user["refresh_token"]}
    )
    assert out.status_code == 204

    # Cannot mint a new access token now.
    after = await http_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": user["refresh_token"]}
    )
    assert after.status_code == 401

    # Idempotent: second logout call is still 204.
    out2 = await http_client.post(
        "/api/v1/auth/logout", json={"refresh_token": user["refresh_token"]}
    )
    assert out2.status_code == 204


# 5. /auth/logout-all kills every session.


async def test_logout_all_revokes_every_active_refresh_token(http_client) -> None:
    user = await _login(http_client)
    # Open a second session by logging in again.
    other_login = await http_client.post(
        "/api/v1/auth/login",
        json={"email": user["email"], "password": user["password"]},
    )
    other_refresh = other_login.json()["refresh_token"]

    # Logout-all uses the access token of one session.
    out_all = await http_client.post(
        "/api/v1/auth/logout-all", headers=user["headers"]
    )
    assert out_all.status_code == 204

    # Both refresh tokens are now revoked.
    for rt in (user["refresh_token"], other_refresh):
        r = await http_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": rt}
        )
        assert r.status_code == 401, (
            f"refresh token still works after logout-all: {rt[:20]}..."
        )


# 6. Pre-014 token (no jti) is rejected.


async def test_refresh_token_without_jti_is_rejected(http_client) -> None:
    """A JWT minted without a `jti` claim must be rejected by /auth/refresh."""
    from app.config import settings as cfg
    from app.core.security import ALGORITHM
    from datetime import datetime, timedelta
    from jose import jwt

    user = await _login(http_client)
    legacy_token = jwt.encode(
        {
            "exp": datetime.utcnow() + timedelta(days=7),
            "sub": user["user_id"],
            "type": "refresh",
        },
        cfg.secret_key,
        algorithm=ALGORITHM,
    )
    r = await http_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": legacy_token}
    )
    assert r.status_code == 401
    assert "jti" in r.json().get("detail", "").lower()
