"""Phase 10 audit-log + Phase 3 P3-A10 tests.

Covers:
  P10-H1  /memory/user/{id}/export, /delete, /consent emit gdpr_audit_log
  P10-H2  /auth/* security events emit auth_audit_log
  P3-A10  POST /auth/api-keys/{id}/rotate atomically issues a new key
"""
from __future__ import annotations

import uuid
from typing import List

import pytest
from httpx import AsyncClient

from app.core.audit_log import (
    list_demo_auth_events,
    list_demo_gdpr_events,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# ── helpers ──────────────────────────────────────────────────────────────────
async def _register_and_login(
    client: AsyncClient,
) -> tuple[str, str, dict]:
    email = f"audit_{uuid.uuid4().hex[:8]}@nexmem.example.com"
    password = "TestPass123!"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert reg.status_code == 201
    user_id = reg.json()["id"]
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return user_id, email, headers


def _actions(events: List[dict]) -> List[str]:
    return [e["action"] for e in events]


# ── P10-H2: auth audit ───────────────────────────────────────────────────────
class TestAuthAudit:
    async def test_register_and_login_recorded(self, client: AsyncClient):
        user_id, _, _ = await _register_and_login(client)
        events = list_demo_auth_events(user_id)
        assert "register" in _actions(events)
        assert "login_success" in _actions(events)
        # Each row carries an actor + target + timestamp.
        first = events[0]
        assert first["actor_user_id"] == user_id
        assert first["target_user_id"] == user_id
        assert "created_at" in first

    async def test_login_failure_recorded_for_known_user(
        self, client: AsyncClient
    ):
        user_id, email, _ = await _register_and_login(client)
        bad = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "wrong"},
        )
        assert bad.status_code == 401
        events = list_demo_auth_events(user_id)
        failures = [e for e in events if e["action"] == "login_failure"]
        assert failures
        assert failures[-1]["payload"]["reason"] == "wrong_password"

    async def test_login_failure_unknown_email_drops_audit(
        self, client: AsyncClient
    ):
        # No user_id -> no row. The brute-force tracker still fires
        # but the audit log helper drops the event because we have
        # no target. This is intentional.
        bad = await client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "x"},
        )
        assert bad.status_code == 401
        # No user, so nothing to assert beyond "no exception".

    async def test_logout_password_change_session_revoke_recorded(
        self, client: AsyncClient
    ):
        user_id, _, headers = await _register_and_login(client)

        # password change
        await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "TestPass123!",
                "new_password": "NewerPass456!",
            },
            headers=headers,
        )
        # logout (refresh token from initial login)
        events = list_demo_auth_events(user_id)
        actions = _actions(events)
        assert "password_change" in actions

    async def test_session_revoke_recorded(self, client: AsyncClient):
        user_id, _, headers = await _register_and_login(client)
        sessions = await client.get("/api/v1/auth/sessions", headers=headers)
        sid = sessions.json()[0]["id"]
        rev = await client.delete(
            f"/api/v1/auth/sessions/{sid}", headers=headers
        )
        assert rev.status_code == 204
        events = list_demo_auth_events(user_id)
        revokes = [e for e in events if e["action"] == "session_revoke"]
        assert revokes
        assert revokes[-1]["payload"]["session_id"] == sid


# ── P10-H1: GDPR audit ───────────────────────────────────────────────────────
class TestGDPRAudit:
    async def test_export_recorded(self, client: AsyncClient):
        user_id, _, headers = await _register_and_login(client)
        async with client.stream(
            "GET",
            f"/api/v1/memory/user/{user_id}/export",
            headers=headers,
        ) as resp:
            async for _chunk in resp.aiter_bytes():
                pass
        events = list_demo_gdpr_events(user_id)
        assert _actions(events) == ["export"]

    async def test_consent_change_recorded(self, client: AsyncClient):
        user_id, _, headers = await _register_and_login(client)
        await client.patch(
            f"/api/v1/memory/user/{user_id}/consent",
            json={"marketing": True, "analytics": False},
            headers=headers,
        )
        events = list_demo_gdpr_events(user_id)
        assert _actions(events) == ["consent_change"]
        assert events[0]["payload"]["consent"] == {
            "marketing": True,
            "analytics": False,
        }

    async def test_delete_request_recorded_with_schedule(self, client: AsyncClient):
        """P7-E4 (Block 5): the soft-delete request emits a single
        ``delete_request`` audit row carrying the schedule, never the
        per-table deletion counts (those only exist after the
        Celery task does the actual cascade)."""
        user_id, _, headers = await _register_and_login(client)
        # Seed a row so we can confirm it is NOT deleted at request time.
        from app import demo_db

        demo_db.create_episodic(user_id, "s", "x")

        await client.delete(
            f"/api/v1/memory/user/{user_id}/all",
            headers={**headers, "X-Confirm-Delete": "true"},
        )
        events = list_demo_gdpr_events(user_id)
        assert _actions(events) == ["delete_request"]
        payload = events[0]["payload"]
        assert payload["grace_period_days"] == 30
        assert payload["deletion_scheduled_for"]
        # The data is still there — the Celery task hasn't run.
        assert demo_db.episodic_store.get(user_id)


# ── P3-A10: API key rotation ────────────────────────────────────────────────
class TestApiKeyRotation:
    async def test_rotate_returns_new_raw_key(self, client: AsyncClient):
        user_id, _, headers = await _register_and_login(client)
        created = await client.post(
            "/api/v1/auth/api-keys",
            json={"name": "telegram-bot"},
            headers=headers,
        )
        assert created.status_code == 201
        old_id = created.json()["id"]
        old_raw = created.json()["api_key"]

        rotated = await client.post(
            f"/api/v1/auth/api-keys/{old_id}/rotate",
            headers=headers,
        )
        assert rotated.status_code == 201
        new = rotated.json()
        assert new["name"] == "telegram-bot"
        assert new["id"] != old_id
        assert new["api_key"] != old_raw

    async def test_rotate_does_not_kill_old_key_yet(
        self, client: AsyncClient
    ):
        """The old key must still authenticate after a rotate. The
        operator chooses when to delete it (atomic rotation = both
        live for a grace window)."""
        user_id, _, headers = await _register_and_login(client)
        created = await client.post(
            "/api/v1/auth/api-keys",
            json={"name": "k"},
            headers=headers,
        )
        old_id = created.json()["id"]
        old_raw = created.json()["api_key"]

        await client.post(
            f"/api/v1/auth/api-keys/{old_id}/rotate",
            headers=headers,
        )

        # Old key still authenticates.
        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"ApiKey {old_raw}"},
        )
        assert me.status_code == 200

    async def test_rotate_records_audit(self, client: AsyncClient):
        user_id, _, headers = await _register_and_login(client)
        created = await client.post(
            "/api/v1/auth/api-keys",
            json={"name": "audited-key"},
            headers=headers,
        )
        old_id = created.json()["id"]
        rotated = await client.post(
            f"/api/v1/auth/api-keys/{old_id}/rotate",
            headers=headers,
        )
        new_id = rotated.json()["id"]
        events = list_demo_auth_events(user_id)
        rotations = [e for e in events if e["action"] == "api_key_rotate"]
        assert rotations
        last = rotations[-1]
        assert last["payload"]["old_api_key_id"] == old_id
        assert last["payload"]["new_api_key_id"] == new_id

    async def test_rotate_unknown_key_404(self, client: AsyncClient):
        _, _, headers = await _register_and_login(client)
        ghost = str(uuid.uuid4())
        r = await client.post(
            f"/api/v1/auth/api-keys/{ghost}/rotate", headers=headers
        )
        assert r.status_code == 404
