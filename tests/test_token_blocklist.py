"""Phase 3 P3-A5 access-token blocklist tests.

Closes R-102 (no real session revocation for access tokens).

Each test fails before the fix and passes after. Runs in DEMO_MODE
with a fake Redis so no Postgres / Redis services are required.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict

import pytest
from httpx import AsyncClient
from jose import JWTError

from app.config import settings


pytestmark = [pytest.mark.unit]


# ── Fake Redis for hermetic tests ────────────────────────────────────────────
class _FakeRedis:
    """Subset of redis.Redis used by token_blocklist."""

    def __init__(self) -> None:
        self.kv: Dict[str, str] = {}
        self.ttls: Dict[str, int] = {}

    def setex(self, name, ttl, value):  # noqa: ANN001
        self.kv[name] = value
        self.ttls[name] = int(ttl)
        return True

    def exists(self, name):  # noqa: ANN001
        return 1 if name in self.kv else 0


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0")
    from app.core import token_blocklist

    monkeypatch.setattr(token_blocklist, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def no_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", None)
    from app.core import token_blocklist

    monkeypatch.setattr(token_blocklist, "_redis_client", lambda: None)


# ── Unit tests for the helpers ───────────────────────────────────────────────
class TestRevokeIsRevoked:
    def test_revoke_then_is_revoked(self, fake_redis):
        from app.core.token_blocklist import is_revoked, revoke

        jti = "abc123"
        assert is_revoked(jti) is False
        # ``exp`` 60 s in the future.
        exp = int(datetime.utcnow().timestamp()) + 60
        assert revoke(jti, exp=exp) is True
        assert is_revoked(jti) is True

    def test_revoke_uses_remaining_lifetime_for_ttl(self, fake_redis):
        from app.core.token_blocklist import revoke

        jti = "ttl-test"
        # exp 30 s ahead → TTL must be ~30 (not the full 4 h cap).
        exp = int(datetime.utcnow().timestamp()) + 30
        assert revoke(jti, exp=exp) is True
        assert 28 <= fake_redis.ttls[f"access_blocklist:{jti}"] <= 32

    def test_revoke_falls_back_to_setting_when_no_exp(
        self, fake_redis, monkeypatch
    ):
        monkeypatch.setattr(settings, "access_token_expire_hours", 2)
        from app.core.token_blocklist import revoke

        revoke("no-exp")
        ttl = fake_redis.ttls["access_blocklist:no-exp"]
        # 2 h = 7200 s.
        assert ttl == 7200

    def test_revoke_fails_closed_when_no_redis(self, no_redis):
        from app.core.token_blocklist import revoke

        # Returns False so callers can surface a clear error rather
        # than silently leaving the token alive.
        assert revoke("nope") is False

    def test_is_revoked_fails_open_when_no_redis(self, no_redis):
        from app.core.token_blocklist import is_revoked

        # Returns False so a Redis outage does not log out every user.
        assert is_revoked("anything") is False


# ── decode_token integration ─────────────────────────────────────────────────
class TestDecodeTokenChecksBlocklist:
    def test_blocklisted_access_token_raises(self, fake_redis):
        from app.core.security import create_access_token, decode_token
        from app.core.token_blocklist import revoke

        token = create_access_token(subject=str(uuid.uuid4()))
        # Decode once to grab the jti.
        from jose import jwt as _jwt
        from app.core.security import ALGORITHM

        payload = _jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
        )
        jti = payload["jti"]
        exp = payload["exp"]
        assert revoke(jti, exp=exp) is True

        with pytest.raises(JWTError):
            decode_token(token)

    def test_unrevoked_token_decodes_normally(self, fake_redis):
        from app.core.security import create_access_token, decode_token

        token = create_access_token(subject=str(uuid.uuid4()))
        payload = decode_token(token)
        assert payload["type"] == "access"
        assert "jti" in payload  # P3-A5: every access token carries a jti


# ── End-to-end via the HTTP layer ────────────────────────────────────────────
@pytest.mark.asyncio
class TestRevokeViaHttp:
    async def test_revoke_current_token_rejects_subsequent_requests(
        self, client: AsyncClient, fake_redis
    ):
        # 1. Register + login.
        email = f"rev_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!"},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "TestPass123!"},
        )
        access = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {access}"}

        # 2. /me works.
        ok = await client.get("/api/v1/auth/me", headers=headers)
        assert ok.status_code == 200

        # 3. Revoke the token via the new endpoint.
        rev = await client.post(
            "/api/v1/auth/revoke-current-token", headers=headers
        )
        assert rev.status_code == 204

        # 4. Same token now 401s on /me.
        gone = await client.get("/api/v1/auth/me", headers=headers)
        assert gone.status_code == 401

    async def test_change_password_blocklists_current_token(
        self, client: AsyncClient, fake_redis
    ):
        email = f"chgrev_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!"},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "TestPass123!"},
        )
        access = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {access}"}

        # Change the password — same handler should also blocklist
        # the access token in flight (P3-A5).
        r = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "TestPass123!",
                "new_password": "NewerPass456!",
            },
            headers=headers,
        )
        assert r.status_code == 200

        # The original access token is now revoked.
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 401

    async def test_revoke_current_503_when_redis_unavailable(
        self, client: AsyncClient, no_redis
    ):
        """If Redis is down the route MUST surface a clear error
        rather than pretend the token is dead. Otherwise an attacker
        with a Redis-down operator gets free reign."""
        email = f"rev503_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!"},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "TestPass123!"},
        )
        access = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {access}"}

        r = await client.post(
            "/api/v1/auth/revoke-current-token", headers=headers
        )
        assert r.status_code == 503
        assert "logout-all" in r.json()["detail"]
