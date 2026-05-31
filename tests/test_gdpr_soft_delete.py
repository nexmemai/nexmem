"""P7-E4 (Block 5): GDPR soft-delete grace period.

Four tests covering the new contract:

* ``test_delete_request_returns_scheduled_response`` — DELETE is
  no longer an immediate cascade; it returns the schedule envelope.
* ``test_delete_request_sets_user_inactive`` — the account is
  frozen at request time, so authenticated routes 401 immediately.
* ``test_cancel_deletion_within_grace_period_restores_access`` — the
  cancel-deletion route reactivates the user and clears the schedule.
* ``test_cancel_fails_gracefully_when_no_deletion_pending`` — calling
  cancel without a pending deletion returns 400, not 500.

Runs entirely in DEMO_MODE.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Tuple

import pytest
from httpx import AsyncClient

from app import demo_db
from app.core import demo_auth


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _register_login_seed(
    client: AsyncClient,
) -> Tuple[str, str, str, dict]:
    """Register a new user, log in, seed one row of each kind, return
    ``(user_id, email, password, headers)``."""
    email = f"sd_{uuid.uuid4().hex[:8]}@nexmem.example.com"
    password = "SoftDelete123!"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert reg.status_code == 201, reg.text
    user_id = reg.json()["id"]

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    demo_db.create_episodic(
        user_id, "session_sd", "an episode", tags=["sd"]
    )
    demo_db.create_semantic(
        user_id, vector=[0.1] * 384, summary="s", content_preview="s"
    )
    demo_db.upsert_procedural(user_id, settings={"theme": "x"}, workflows=[])
    demo_db.create_node(user_id, label="A", node_type="concept")
    return user_id, email, password, headers


async def _request_soft_delete(
    client: AsyncClient, user_id: str, headers: dict
) -> dict:
    r = await client.delete(
        f"/api/v1/memory/user/{user_id}/all",
        headers={**headers, "X-Confirm-Delete": "true"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── 1 ─────────────────────────────────────────────────────────────────────────
async def test_delete_request_returns_scheduled_response(client: AsyncClient):
    """DELETE returns the new envelope: ``scheduled_deletion: True``,
    a future ``deletion_date``, ``grace_period_days: 30``, and a
    ``cancel_url`` the client can hit during the grace period."""
    user_id, _, _, headers = await _register_login_seed(client)
    body = await _request_soft_delete(client, user_id, headers)

    assert body["scheduled_deletion"] is True
    assert body["grace_period_days"] == 30
    iso = body["deletion_date"]
    parsed = datetime.fromisoformat(iso)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed - datetime.now(timezone.utc)
    # Should be roughly 30 days out — accept anything between 29 and 31
    # to absorb sub-second clock drift between the route and this assert.
    assert timedelta(days=29) < delta < timedelta(days=31)

    assert body["cancel_before"] == iso
    assert body["cancel_url"].endswith(
        f"/memory/user/{user_id}/cancel-deletion"
    )

    # Memory rows are NOT yet deleted — that's the whole point of
    # the grace period.
    assert demo_db.episodic_store.get(user_id)
    assert demo_db.semantic_store.get(user_id)


# ── 2 ─────────────────────────────────────────────────────────────────────────
async def test_delete_request_sets_user_inactive(client: AsyncClient):
    """At request time the user is set ``is_active=False`` so every
    other authenticated route returns 401 even though the row +
    memories survive the grace period."""
    user_id, email, _, headers = await _register_login_seed(client)
    await _request_soft_delete(client, user_id, headers)

    # Demo store: is_active flipped to False; deletion_scheduled_for set.
    user = demo_auth.get_user_by_email(email)
    assert user is not None
    assert user.is_active is False
    assert user.deletion_scheduled_for is not None
    assert user.deletion_requested_at is not None

    # Same JWT now 401s on any authenticated route.
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 401


# ── 3 ─────────────────────────────────────────────────────────────────────────
async def test_cancel_deletion_within_grace_period_restores_access(
    client: AsyncClient,
):
    """POST /cancel-deletion during the grace period clears both
    timestamps and reactivates the user. The same access token the
    user already holds works again on /me."""
    user_id, email, _, headers = await _register_login_seed(client)
    await _request_soft_delete(client, user_id, headers)

    # The same Bearer token must work for cancel even though the
    # user is is_active=False — this is what get_user_in_grace_period
    # exists for.
    r = await client.post(
        f"/api/v1/memory/user/{user_id}/cancel-deletion",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"cancelled": True, "account_restored": True}

    # State is fully restored.
    user = demo_auth.get_user_by_email(email)
    assert user.is_active is True
    assert user.deletion_requested_at is None
    assert user.deletion_scheduled_for is None

    # /me works again.
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == email


# ── 4 ─────────────────────────────────────────────────────────────────────────
async def test_cancel_fails_gracefully_when_no_deletion_pending(
    client: AsyncClient,
):
    """Calling cancel without a pending deletion returns a clean 400,
    not a 500. The user must still be authenticated for the route
    to even reach the no-pending-deletion branch."""
    user_id, _, _, headers = await _register_login_seed(client)

    # No DELETE was issued, so deletion_scheduled_for is NULL.
    r = await client.post(
        f"/api/v1/memory/user/{user_id}/cancel-deletion",
        headers=headers,
    )
    assert r.status_code == 400, r.text
    assert "no pending deletion" in r.json()["detail"].lower()

    # Cancel for someone else's account is 403, not 400.
    other_id = str(uuid.uuid4())
    r2 = await client.post(
        f"/api/v1/memory/user/{other_id}/cancel-deletion",
        headers=headers,
    )
    assert r2.status_code == 403
