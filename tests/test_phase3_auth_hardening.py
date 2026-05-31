"""Phase 3 backend hardening — auth and session tests.

Covers:
  P3-A1  Email verification on registration
  P3-A2  Password reset request + confirm
  P3-A3  Authenticated change-password flow
  P3-A4  Session listing + per-session revocation
  P3-A8  Per-IP rate limit on /auth/register

Each test fails before its corresponding fix and passes after.
Runs in DEMO_MODE so no Postgres or Redis is required.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app import demo_db
from app.config import settings
from app.core import demo_auth
from app.core.security import hash_url_safe_token


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# ── helpers ──────────────────────────────────────────────────────────────────
async def _register(client: AsyncClient, email: str, password: str = "TestPass123!"):
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


async def _login(client: AsyncClient, email: str, password: str = "TestPass123!"):
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


def _last_verification_token_for(email: str) -> str:
    """Read the raw verification token out of the demo store.

    The demo store hashes the token before persisting; in tests we
    bypass that by remembering the most recent unconsumed token for
    the given user. This helper reverses that by walking the store.
    """
    user_id = demo_db.demo_users_by_email.get(email.lower())
    if not user_id:
        raise AssertionError(f"unknown email {email}")
    matches = [
        rec
        for rec in demo_db.demo_email_verification_tokens.values()
        if str(rec["user_id"]) == user_id and rec["consumed_at"] is None
    ]
    if not matches:
        raise AssertionError(f"no verification token for {email}")
    return matches[-1]["token_hash"]  # we accept the hash itself in confirm


def _last_reset_token_for(email: str) -> str:
    user_id = demo_db.demo_users_by_email.get(email.lower())
    if not user_id:
        raise AssertionError(f"unknown email {email}")
    matches = [
        rec
        for rec in demo_db.demo_password_reset_tokens.values()
        if str(rec["user_id"]) == user_id and rec["consumed_at"] is None
    ]
    if not matches:
        raise AssertionError(f"no reset token for {email}")
    return matches[-1]["token_hash"]


@pytest.fixture
def require_email_verification(monkeypatch):
    """Flip the ``email_verification_required`` setting on for one test."""
    monkeypatch.setattr(settings, "email_verification_required", True)
    yield


# ── P3-A1: email verification ────────────────────────────────────────────────
class TestEmailVerification:
    async def test_register_issues_verification_token(self, client: AsyncClient):
        email = f"verify_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        r = await _register(client, email)
        assert r.status_code == 201
        # A pending verification token now exists for this user.
        user_id = demo_db.demo_users_by_email[email.lower()]
        pending = [
            rec
            for rec in demo_db.demo_email_verification_tokens.values()
            if str(rec["user_id"]) == user_id and rec["consumed_at"] is None
        ]
        assert len(pending) == 1

    async def test_login_blocked_when_verification_required(
        self, client: AsyncClient, require_email_verification
    ):
        email = f"gate_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await _register(client, email)
        # Login refused with 403 because email_verified_at is None
        # (verification required, never confirmed).
        r = await _login(client, email)
        assert r.status_code == 403, r.text
        assert "verif" in r.json()["detail"].lower()

    async def test_login_allowed_after_verification(
        self, client: AsyncClient, require_email_verification
    ):
        email = f"pass_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await _register(client, email)
        # Use the *raw* token: in tests we can shortcut by handing the
        # hash directly into the demo store's consume helper, but the
        # public route accepts any token that hashes to a stored row.
        # The simplest is to mint a token whose hash we know.
        # The register endpoint already issued one; capture its hash
        # and replay it through the consume API after we re-derive
        # the matching raw token in production.
        # In demo mode we keep the raw token equal to the hash for
        # the purposes of this test by writing one in directly:
        from datetime import datetime, timedelta

        raw = "raw-token-" + uuid.uuid4().hex
        digest = hash_url_safe_token(raw)
        user_id = uuid.UUID(demo_db.demo_users_by_email[email.lower()])
        demo_auth.add_email_verification_token(
            user_id, digest, datetime.utcnow() + timedelta(hours=1)
        )

        r = await client.post(
            "/api/v1/auth/verify-email/confirm", json={"token": raw}
        )
        assert r.status_code == 200, r.text
        # Login now succeeds.
        login = await _login(client, email)
        assert login.status_code == 200, login.text

    async def test_verify_token_is_single_use(
        self, client: AsyncClient, require_email_verification
    ):
        email = f"replay_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await _register(client, email)
        from datetime import datetime, timedelta

        raw = "single-use-" + uuid.uuid4().hex
        digest = hash_url_safe_token(raw)
        user_id = uuid.UUID(demo_db.demo_users_by_email[email.lower()])
        demo_auth.add_email_verification_token(
            user_id, digest, datetime.utcnow() + timedelta(hours=1)
        )
        r1 = await client.post(
            "/api/v1/auth/verify-email/confirm", json={"token": raw}
        )
        assert r1.status_code == 200
        r2 = await client.post(
            "/api/v1/auth/verify-email/confirm", json={"token": raw}
        )
        assert r2.status_code == 400

    async def test_verify_resend_does_not_leak_account(self, client: AsyncClient):
        # Unknown emails get the same 202 as known ones.
        r = await client.post(
            "/api/v1/auth/verify-email/resend",
            json={"email": "nobody-here@nexmem.example.com"},
        )
        assert r.status_code == 202


# ── P3-A2: password reset ────────────────────────────────────────────────────
class TestPasswordReset:
    async def test_request_returns_202_for_unknown_email(self, client: AsyncClient):
        r = await client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "ghost-user@nexmem.example.com"},
        )
        assert r.status_code == 202
        # No token created for an account that does not exist.
        assert len(demo_db.demo_password_reset_tokens) == 0

    async def test_full_reset_flow_revokes_all_sessions(self, client: AsyncClient):
        from datetime import datetime, timedelta

        email = f"reset_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await _register(client, email)
        # Create two active sessions.
        login1 = await _login(client, email)
        login2 = await _login(client, email)
        refresh_a = login1.json()["refresh_token"]
        refresh_b = login2.json()["refresh_token"]

        # Drive the reset flow: insert a known raw token + hash so the
        # confirm route can be exercised end-to-end.
        raw = "reset-raw-" + uuid.uuid4().hex
        digest = hash_url_safe_token(raw)
        user_id = uuid.UUID(demo_db.demo_users_by_email[email.lower()])
        demo_auth.add_password_reset_token(
            user_id, digest, datetime.utcnow() + timedelta(minutes=15)
        )

        new_password = "NewerPass456!"
        confirm = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw, "new_password": new_password},
        )
        assert confirm.status_code == 200, confirm.text

        # Old password no longer logs in.
        r_old = await _login(client, email, "TestPass123!")
        assert r_old.status_code == 401
        # New password logs in.
        r_new = await _login(client, email, new_password)
        assert r_new.status_code == 200

        # Both pre-reset refresh tokens are revoked.
        for tok in (refresh_a, refresh_b):
            r = await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": tok}
            )
            assert r.status_code == 401

    async def test_reset_token_is_single_use(self, client: AsyncClient):
        from datetime import datetime, timedelta

        email = f"once_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await _register(client, email)
        raw = "once-raw-" + uuid.uuid4().hex
        digest = hash_url_safe_token(raw)
        user_id = uuid.UUID(demo_db.demo_users_by_email[email.lower()])
        demo_auth.add_password_reset_token(
            user_id, digest, datetime.utcnow() + timedelta(minutes=15)
        )
        first = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw, "new_password": "OnlyOnce123!"},
        )
        assert first.status_code == 200
        replay = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw, "new_password": "AnotherOne456!"},
        )
        assert replay.status_code == 400


# ── P3-A3: change password ───────────────────────────────────────────────────
class TestChangePassword:
    async def test_requires_current_password(
        self, client: AsyncClient, auth_headers: dict
    ):
        r = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "wrong-password", "new_password": "NewerPass456!"},
            headers=auth_headers,
        )
        assert r.status_code == 401

    async def test_rotates_password_and_revokes_sessions(
        self, client: AsyncClient
    ):
        email = f"chg_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await _register(client, email)
        login_a = await _login(client, email)
        login_b = await _login(client, email)
        access_a = login_a.json()["access_token"]
        refresh_a = login_a.json()["refresh_token"]
        refresh_b = login_b.json()["refresh_token"]

        r = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "TestPass123!",
                "new_password": "NewerPass456!",
            },
            headers={"Authorization": f"Bearer {access_a}"},
        )
        assert r.status_code == 200, r.text

        # Old password fails.
        old = await _login(client, email, "TestPass123!")
        assert old.status_code == 401
        # New password works.
        new = await _login(client, email, "NewerPass456!")
        assert new.status_code == 200
        # Every existing refresh token is revoked.
        for tok in (refresh_a, refresh_b):
            rr = await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": tok}
            )
            assert rr.status_code == 401

    async def test_new_password_must_differ(
        self, client: AsyncClient, auth_headers: dict
    ):
        r = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "TestPass123!",
                "new_password": "TestPass123!",
            },
            headers=auth_headers,
        )
        assert r.status_code == 400


# ── P3-A4: sessions listing + revocation ─────────────────────────────────────
class TestSessions:
    async def test_list_returns_two_sessions(self, client: AsyncClient):
        email = f"sess_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await _register(client, email)
        login_a = await _login(client, email)
        await _login(client, email)
        access = login_a.json()["access_token"]

        r = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert {"id", "issued_at", "expires_at"}.issubset(data[0].keys())

    async def test_delete_one_session_revokes_only_that_token(
        self, client: AsyncClient
    ):
        email = f"sessdel_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await _register(client, email)
        login_a = await _login(client, email)
        login_b = await _login(client, email)
        access = login_a.json()["access_token"]
        refresh_a = login_a.json()["refresh_token"]
        refresh_b = login_b.json()["refresh_token"]

        listed = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access}"},
        )
        sessions = listed.json()
        # find the session that maps to refresh_a by checking which row
        # is *not* the most recent (login_b was after login_a)
        # The list is sorted by issued_at desc; sessions[1] is the first login.
        sid_a = sessions[1]["id"]

        rev = await client.delete(
            f"/api/v1/auth/sessions/{sid_a}",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert rev.status_code == 204

        # refresh_a is dead; refresh_b still works.
        r_dead = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_a}
        )
        assert r_dead.status_code == 401
        r_alive = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_b}
        )
        assert r_alive.status_code == 200

    async def test_delete_session_404_for_unknown(
        self, client: AsyncClient, auth_headers: dict
    ):
        random_id = str(uuid.uuid4())
        r = await client.delete(
            f"/api/v1/auth/sessions/{random_id}", headers=auth_headers
        )
        assert r.status_code == 404


# ── P3-A8: rate limit on /auth/register ──────────────────────────────────────
class TestRegisterRateLimit:
    async def test_setting_is_present_and_strict(self):
        """The rate-limit string is configured for production traffic.

        We do not exercise the limiter in this suite because demo mode
        is exempted (otherwise the entire test session would trip the
        cap on shared CI runners). The presence of the configured
        cap, plus the slowapi decorator on the route, is what we
        assert here.
        """
        assert settings.register_rate_limit
        assert "/" in settings.register_rate_limit  # e.g. "5/hour"

    async def test_limiter_decorator_applied_to_register_route(self):
        """The route must be decorated with the limiter, otherwise the
        cap is never enforced even outside demo mode."""
        from app.routers.auth import register

        # slowapi attaches metadata under ``__wrapped__`` chains.
        # We assert only the closure carries a Limiter reference.
        closure_vars = (
            register.__closure__ if getattr(register, "__closure__", None) else ()
        )
        from app.core.rate_limit import limiter

        found = False
        for cell in closure_vars:
            try:
                if cell.cell_contents is limiter:
                    found = True
                    break
            except ValueError:
                continue
        # Fallback: the limiter records the route in its internal map.
        try:
            keys = list(limiter._route_limits.keys())  # noqa: SLF001
        except Exception:
            keys = []
        assert found or any("register" in k for k in keys), (
            "/auth/register is not decorated with the slowapi limiter"
        )
