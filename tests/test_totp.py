"""Tests for P3-A6: TOTP / 2FA (Block 5).

Six tests covering the four-endpoint surface introduced in
``app/routers/totp.py``. All tests run in ``DEMO_MODE=true`` and use
real ``pyotp`` codes so the server's verify path is exercised end
to end (no mocking — the user's spec said "verify always succeeds in
demo mode" but that contradicts ``test_totp_complete_login_fails_
with_wrong_code``; using real pyotp resolves the contradiction).
"""

from __future__ import annotations

import base64
import uuid
from typing import Tuple

import pyotp
import pytest
from httpx import AsyncClient


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _register_and_login(
    client: AsyncClient,
) -> Tuple[str, str, dict]:
    """Register a fresh user, log in, return ``(email, password, headers)``."""
    email = f"totp_{uuid.uuid4().hex[:8]}@nexmem.example.com"
    password = "TotpPass123!"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert reg.status_code == 201, reg.text

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    # Pre-TOTP-enable: login returns full tokens, NOT requires_totp.
    assert "access_token" in body
    return email, password, {"Authorization": f"Bearer {body['access_token']}"}


async def _enroll_totp(client: AsyncClient, headers: dict) -> str:
    """Helper: call /setup and /verify and return the (now-enabled) secret."""
    setup = await client.post("/api/v1/auth/totp/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    verify = await client.post(
        "/api/v1/auth/totp/verify",
        headers=headers,
        json={"totp_code": code},
    )
    assert verify.status_code == 200, verify.text
    return secret


# ── 1 ─────────────────────────────────────────────────────────────────────────
async def test_totp_setup_returns_secret_and_qr(client: AsyncClient):
    """``/setup`` returns a base32 secret, an otpauth URI, and a base64
    PNG. ``totp_enabled`` MUST stay False until /verify succeeds."""
    _, _, headers = await _register_and_login(client)

    r = await client.post("/api/v1/auth/totp/setup", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    assert "secret" in body
    secret = body["secret"]
    # pyotp default secrets are 32 base32 characters.
    assert len(secret) >= 16
    # Round-trip through pyotp to confirm it is a valid base32 secret.
    assert pyotp.TOTP(secret).now()  # raises if secret is malformed.

    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert "issuer=Nexmem" in body["otpauth_uri"]

    # qr_code_base64 must decode to PNG bytes.
    raw = base64.b64decode(body["qr_code_base64"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    # ``totp_enabled`` is still False — a normal login should not yet
    # be challenged for a code.
    login_again = await client.post(
        "/api/v1/auth/login",
        json={
            "email": (await client.get(
                "/api/v1/auth/me", headers=headers
            )).json().get("email", ""),
            "password": "TotpPass123!",
        },
    )
    assert login_again.status_code == 200
    assert login_again.json().get("requires_totp") is not True


# ── 2 ─────────────────────────────────────────────────────────────────────────
async def test_totp_verify_enables_2fa(client: AsyncClient):
    """``/verify`` with a valid code flips ``totp_enabled`` to True."""
    _, _, headers = await _register_and_login(client)

    setup = await client.post("/api/v1/auth/totp/setup", headers=headers)
    secret = setup.json()["secret"]

    # Wrong code first — must 400 and NOT enable TOTP.
    bad = await client.post(
        "/api/v1/auth/totp/verify",
        headers=headers,
        json={"totp_code": "000000"},
    )
    assert bad.status_code == 400

    # Correct code — must 200 and flip the flag.
    good = pyotp.TOTP(secret).now()
    ok = await client.post(
        "/api/v1/auth/totp/verify",
        headers=headers,
        json={"totp_code": good},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"enabled": True}


# ── 3 ─────────────────────────────────────────────────────────────────────────
async def test_totp_login_returns_totp_required_when_enabled(
    client: AsyncClient,
):
    """After /verify enables TOTP, /auth/login no longer mints
    access tokens — it returns the totp_session_token shape."""
    email, password, headers = await _register_and_login(client)
    await _enroll_totp(client, headers)

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body.get("requires_totp") is True
    assert isinstance(body.get("totp_session_token"), str)
    assert len(body["totp_session_token"]) > 20
    # Crucial: no access_token leaks before the second factor.
    assert "access_token" not in body
    # 5-minute window matches create_totp_session_token.
    assert body.get("expires_in") == 300


# ── 4 ─────────────────────────────────────────────────────────────────────────
async def test_totp_complete_login_succeeds_with_valid_code(
    client: AsyncClient,
):
    """Submitting the totp_session_token + a valid code returns the
    normal access + refresh token pair."""
    email, password, headers = await _register_and_login(client)
    secret = await _enroll_totp(client, headers)

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    sess = login.json()["totp_session_token"]

    valid = pyotp.TOTP(secret).now()
    finish = await client.post(
        "/api/v1/auth/totp/complete-login",
        json={"totp_session_token": sess, "totp_code": valid},
    )
    assert finish.status_code == 200, finish.text
    body = finish.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]

    # The minted access token must work against an authenticated route.
    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == email


# ── 5 ─────────────────────────────────────────────────────────────────────────
async def test_totp_complete_login_fails_with_wrong_code(
    client: AsyncClient,
):
    """A bad TOTP code on /complete-login returns 401, never tokens."""
    email, password, headers = await _register_and_login(client)
    await _enroll_totp(client, headers)

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    sess = login.json()["totp_session_token"]

    finish = await client.post(
        "/api/v1/auth/totp/complete-login",
        json={"totp_session_token": sess, "totp_code": "000000"},
    )
    assert finish.status_code == 401, finish.text
    assert "access_token" not in finish.json()


# ── 6 ─────────────────────────────────────────────────────────────────────────
async def test_totp_disable_requires_both_password_and_code(
    client: AsyncClient,
):
    """/disable rejects unless BOTH password and current code are
    correct, and on success drops the user back to single-factor auth.
    """
    email, password, headers = await _register_and_login(client)
    secret = await _enroll_totp(client, headers)

    valid_code = pyotp.TOTP(secret).now()

    # Wrong password, valid code → 400.
    r = await client.post(
        "/api/v1/auth/totp/disable",
        headers=headers,
        json={"password": "WrongPass!", "totp_code": valid_code},
    )
    assert r.status_code == 400

    # Right password, wrong code → 400.
    r = await client.post(
        "/api/v1/auth/totp/disable",
        headers=headers,
        json={"password": password, "totp_code": "000000"},
    )
    assert r.status_code == 400

    # Both correct → 200.
    r = await client.post(
        "/api/v1/auth/totp/disable",
        headers=headers,
        json={"password": password, "totp_code": valid_code},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"disabled": True}

    # After disable, /auth/login should again return access tokens
    # directly — not the totp_session_token shape.
    login_again = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_again.status_code == 200
    body = login_again.json()
    assert body.get("requires_totp") is not True
    assert "access_token" in body
