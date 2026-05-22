"""Quota enforcement against real Redis.

Drives `enforce_write_quota` end-to-end through the FastAPI dependency:
  - Default `free` tier: low limit → expect 429 once exhausted.
  - Different users do not share counters.
  - Counter is keyed by year-month; flushing Redis between tests resets it.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio


async def _episode(http_client: AsyncClient, user, n: int):
    return await http_client.post(
        f"/api/v1/agents/{user['user_id']}/episodes",
        json={
            "session_id": "quota-test",
            "content": f"hit {n}",
            "metadata": {},
            "tags": [],
        },
        headers=user["headers"],
    )


async def test_free_tier_hits_quota_and_returns_429(
    monkeypatch, fresh_user, http_client: AsyncClient, redis_flushed
) -> None:
    # Lower the limit for this test.
    from app.core import quota as quota_mod

    monkeypatch.setattr(quota_mod.settings, "free_monthly_writes", 3)

    # First three writes succeed.
    for i in range(3):
        r = await _episode(http_client, fresh_user, i)
        assert r.status_code in (200, 201), r.text

    # The fourth must be 429 with structured payload.
    over = await _episode(http_client, fresh_user, 4)
    assert over.status_code == 429
    payload = over.json()["detail"]
    assert payload["error"] == "monthly_quota_exceeded"
    assert payload["tier"] == "free"
    assert payload["limit"] == 3
    assert payload["used"] == 4


async def test_different_users_have_separate_counters(
    monkeypatch, http_client: AsyncClient, redis_flushed
) -> None:
    from app.core import quota as quota_mod

    monkeypatch.setattr(quota_mod.settings, "free_monthly_writes", 2)

    # Register two users via the helper in conftest.
    import uuid

    async def _make():
        email = f"q_{uuid.uuid4().hex[:8]}@example.com"
        await http_client.post(
            "/api/v1/auth/register", json={"email": email, "password": "Q!2026demo"}
        )
        login = await http_client.post(
            "/api/v1/auth/login", json={"email": email, "password": "Q!2026demo"}
        )
        token = login.json()["access_token"]
        me = await http_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        return {
            "user_id": me.json()["id"],
            "headers": {"Authorization": f"Bearer {token}"},
        }

    a = await _make()
    b = await _make()

    # Burn user A's quota.
    for i in range(2):
        r = await _episode(http_client, a, i)
        assert r.status_code in (200, 201)
    over = await _episode(http_client, a, 99)
    assert over.status_code == 429

    # User B is unaffected.
    for i in range(2):
        r = await _episode(http_client, b, i)
        assert r.status_code in (200, 201)
