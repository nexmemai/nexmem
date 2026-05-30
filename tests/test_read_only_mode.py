"""Phase 9 read-only kill switch tests (P9-G1).

The kill switch is the on-call engineer's last-resort tool. It must:

* Freeze every state-changing route with 503 + Retry-After.
* Allow read traffic (``GET`` / ``HEAD`` / ``OPTIONS``) through.
* Allow health and metrics so monitoring is unaffected.
* Allow session revocation (``/auth/logout``, ``/auth/logout-all``,
  ``DELETE /auth/sessions/{id}``) so a compromised session can
  still be killed during an incident.
* Default to OFF so flipping the flag is the *only* way to enter
  read-only mode.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.config import settings


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def read_only(monkeypatch):
    monkeypatch.setattr(settings, "read_only", True)
    yield


# ── basic on/off behaviour ───────────────────────────────────────────────────
class TestKillSwitch:
    async def test_default_is_off(self):
        # The default must be False — a missing env var should never
        # accidentally freeze production.
        assert settings.read_only is False

    async def test_post_blocked_when_on(self, client: AsyncClient, read_only):
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": f"a_{uuid.uuid4().hex[:8]}@x.com", "password": "x"},
        )
        assert r.status_code == 503
        body = r.json()
        assert body["code"] == "READ_ONLY_MODE"
        assert r.headers.get("retry-after") == "60"

    async def test_get_passes_when_on(self, client: AsyncClient, read_only):
        r = await client.get("/")
        assert r.status_code == 200

    async def test_put_patch_delete_blocked_when_on(
        self, client: AsyncClient, read_only
    ):
        for method in ("PUT", "PATCH", "DELETE"):
            r = await client.request(
                method, f"/api/v1/whatever-{method.lower()}", json={}
            )
            assert r.status_code == 503, f"{method} not blocked"

    async def test_off_does_not_short_circuit(self, client: AsyncClient):
        # Default settings.read_only=False — the middleware must be a
        # no-op so writes flow as normal.
        r = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"b_{uuid.uuid4().hex[:8]}@nexmem.example.com",
                "password": "TestPass123!",
            },
        )
        assert r.status_code == 201


# ── allowlists for incident-time access ──────────────────────────────────────
class TestAllowlist:
    async def test_health_and_metrics_pass_when_on(
        self, client: AsyncClient, read_only
    ):
        for path in ("/health/live", "/health/ready"):
            r = await client.get(path)
            # Must be 200 or 503 from the readiness probe itself —
            # we only assert it is NOT a kill-switch 503.
            assert r.status_code != 503 or r.json().get("code") != "READ_ONLY_MODE"

    async def test_logout_passes_when_on(
        self, client: AsyncClient, monkeypatch
    ):
        # Set up auth BEFORE flipping read-only — the fixture
        # ``auth_headers`` registers a user, which would itself be
        # blocked by the kill switch.
        email = f"ro_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!"},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "TestPass123!"},
        )
        access = login.json()["access_token"]

        # Now flip the kill switch.
        monkeypatch.setattr(settings, "read_only", True)

        r = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "any-token-the-server-rejects"},
            headers={"Authorization": f"Bearer {access}"},
        )
        # 401 (token does not exist) or 204 (revoked). MUST NOT be 503.
        assert r.status_code != 503

    async def test_session_delete_passes_when_on(
        self, client: AsyncClient, monkeypatch
    ):
        email = f"rosess_{uuid.uuid4().hex[:8]}@nexmem.example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!"},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "TestPass123!"},
        )
        access = login.json()["access_token"]
        monkeypatch.setattr(settings, "read_only", True)

        random_id = str(uuid.uuid4())
        r = await client.delete(
            f"/api/v1/auth/sessions/{random_id}",
            headers={"Authorization": f"Bearer {access}"},
        )
        # 404 expected (session does not exist); MUST NOT be 503.
        assert r.status_code != 503


# ── interaction with body-size cap ───────────────────────────────────────────
class TestMiddlewareOrdering:
    async def test_oversize_request_413s_even_when_read_only(
        self, client: AsyncClient, read_only, monkeypatch
    ):
        """413 (P7-E5) is more specific than 503 (P9-G1). The body
        cap runs before the kill switch so an attacker cannot bury a
        DoS payload inside a frozen-write window."""
        monkeypatch.setattr(settings, "max_request_body_bytes", 1024)
        body = b"a" * 4096
        r = await client.post(
            "/api/v1/auth/register",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 413
