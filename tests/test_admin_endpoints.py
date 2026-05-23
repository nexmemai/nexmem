"""Tests for the /api/v1/admin/* surface (Block 6).

Covers P11-I3 force-logout (Task 2), P11-I2 impersonation (Task 3),
and P11-I4 usage analytics (Task 4). All tests run in
``DEMO_MODE=true``. The admin key is monkeypatched per-test rather
than set globally so the "unconfigured" case stays exercisable.
"""
from __future__ import annotations

import time
import uuid

import pytest
from httpx import AsyncClient

from app.config import settings


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


ADMIN_KEY = "test-admin-key-must-be-at-least-32-chars-long"


@pytest.fixture
def admin_key(monkeypatch):
    """Set the admin api key for the duration of one test."""
    monkeypatch.setattr(settings, "admin_api_key", ADMIN_KEY)
    return ADMIN_KEY


@pytest.fixture
def admin_unconfigured(monkeypatch):
    """Force the admin api key to None for tests that exercise the
    501 inert-default behaviour."""
    monkeypatch.setattr(settings, "admin_api_key", None)


async def _register_and_login(client: AsyncClient) -> tuple[str, str, dict]:
    email = f"admin_{uuid.uuid4().hex[:8]}@nexmem.example.com"
    password = "AdminTest123!"
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


# ── P11-I3 / Task 2 ──────────────────────────────────────────────────────────
async def test_force_logout_requires_admin_key(client: AsyncClient, admin_key):
    """No header → 401 with WWW-Authenticate: X-Admin-Key."""
    user_id = str(uuid.uuid4())
    r = await client.post(f"/api/v1/admin/users/{user_id}/force-logout")
    assert r.status_code == 401
    assert "X-Admin-Key" in r.headers.get("www-authenticate", "")


async def test_force_logout_with_wrong_key_returns_403(
    client: AsyncClient, admin_key
):
    """Wrong key → 403, NOT 401. The distinction matters: an
    unauthenticated caller should retry with a key; a
    wrong-key caller should not retry."""
    user_id = str(uuid.uuid4())
    r = await client.post(
        f"/api/v1/admin/users/{user_id}/force-logout",
        headers={"X-Admin-Key": "WRONG-KEY"},
    )
    assert r.status_code == 403


async def test_force_logout_terminates_user_sessions(
    client: AsyncClient, admin_key
):
    """Happy path. After force-logout:
      * The response declares the number of sessions killed.
      * The user's existing access token can no longer hit /me.
      * The user can still re-login and get a fresh, working token.
    """
    user_id, email, headers = await _register_and_login(client)
    # Sanity: pre-logout the existing access token works.
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200

    # Sleep 1 second so the post-logout re-login token's ``iat``
    # is strictly greater than the cutoff (which uses now() + 1).
    # JWT iat is whole-second resolution.
    time.sleep(1.1)

    r = await client.post(
        f"/api/v1/admin/users/{user_id}/force-logout",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["logged_out"] is True
    assert body["user_id"] == user_id
    # The login above issued one refresh token, so we expect 1.
    assert body["sessions_terminated"] >= 1

    # Same access token: now 401.
    me_after = await client.get("/api/v1/auth/me", headers=headers)
    assert me_after.status_code == 401

    # Re-login works and the new token authenticates.
    time.sleep(1.1)
    relogin = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "AdminTest123!"},
    )
    assert relogin.status_code == 200
    new_headers = {
        "Authorization": f"Bearer {relogin.json()['access_token']}"
    }
    me_again = await client.get("/api/v1/auth/me", headers=new_headers)
    assert me_again.status_code == 200

    # Audit row landed.
    from app.core.audit_log import list_demo_auth_events

    events = list_demo_auth_events(user_id)
    actions = [e["action"] for e in events]
    assert "admin_force_logout" in actions
    last = next(e for e in events if e["action"] == "admin_force_logout")
    assert last["payload"]["actor"] == "admin"
    assert last["payload"]["sessions_terminated"] >= 1


async def test_admin_endpoints_return_501_when_not_configured(
    client: AsyncClient, admin_unconfigured
):
    """When ADMIN_API_KEY is unset every admin route returns 501,
    EVEN if the caller supplies a header. The 501 fires before
    any header validation so an attacker cannot probe the surface
    by varying key values."""
    user_id = str(uuid.uuid4())
    r1 = await client.post(f"/api/v1/admin/users/{user_id}/force-logout")
    assert r1.status_code == 501

    r2 = await client.post(
        f"/api/v1/admin/users/{user_id}/force-logout",
        headers={"X-Admin-Key": "anything"},
    )
    assert r2.status_code == 501




# ── P11-I2 / Task 3 — impersonation ──────────────────────────────────────────
async def test_impersonate_creates_short_lived_token(
    client: AsyncClient, admin_key
):
    """The impersonation token has type=impersonation, sub=target_user_id,
    actor=admin, and expires in the documented 3600 seconds."""
    user_id, _, _ = await _register_and_login(client)

    r = await client.post(
        f"/api/v1/admin/users/{user_id}/impersonate",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expires_in"] == 3600
    assert body["impersonation_token"]
    assert "actor=admin" in body["warning"]

    from app.core.security import decode_token

    payload = decode_token(body["impersonation_token"])
    assert payload["type"] == "impersonation"
    assert payload["sub"] == user_id
    assert payload["actor"] == "admin"
    # The lifetime claim is approximately one hour (allow drift for
    # the call-time deltas).
    iat = int(payload["iat"])
    exp = int(payload["exp"])
    assert 3590 <= (exp - iat) <= 3610

    # Unknown target user → 404.
    bogus = "00000000-0000-0000-0000-000000000999"
    r404 = await client.post(
        f"/api/v1/admin/users/{bogus}/impersonate",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert r404.status_code == 404


async def test_impersonate_logs_to_audit_log(client: AsyncClient, admin_key):
    """Both layers of audit trail are present:

      * one ``admin_impersonation_started`` row at mint time
      * one ``impersonation_request`` row per HTTP call made under
        the token
    """
    user_id, _, _ = await _register_and_login(client)
    r = await client.post(
        f"/api/v1/admin/users/{user_id}/impersonate",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert r.status_code == 200
    token = r.json()["impersonation_token"]

    from app.core.audit_log import list_demo_auth_events

    events = list_demo_auth_events(user_id)
    started = [e for e in events if e["action"] == "admin_impersonation_started"]
    assert len(started) == 1
    assert started[0]["payload"]["actor"] == "admin"
    assert started[0]["payload"]["expires_in"] == 3600

    # Make two requests under the token; expect two impersonation_request
    # rows on top of the one started row.
    headers = {"Authorization": f"Bearer {token}"}
    r1 = await client.get("/api/v1/auth/me", headers=headers)
    r2 = await client.get("/api/v1/auth/sessions", headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200

    events = list_demo_auth_events(user_id)
    requests = [e for e in events if e["action"] == "impersonation_request"]
    assert len(requests) >= 2
    paths = {e["payload"]["path"] for e in requests}
    assert "/api/v1/auth/me" in paths
    assert "/api/v1/auth/sessions" in paths
    for e in requests:
        assert e["payload"]["actor"] == "admin"


async def test_impersonation_token_allows_user_actions(
    client: AsyncClient, admin_key
):
    """The minted token authenticates against routes the target user
    would normally call. The response surface is identical to a real
    user login — routes do not need impersonation-aware code paths."""
    user_id, email, user_headers = await _register_and_login(client)

    r = await client.post(
        f"/api/v1/admin/users/{user_id}/impersonate",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    token = r.json()["impersonation_token"]
    impersonation_headers = {"Authorization": f"Bearer {token}"}

    # /me works and returns the TARGET user's email.
    me = await client.get("/api/v1/auth/me", headers=impersonation_headers)
    assert me.status_code == 200
    assert me.json()["email"] == email

    # The target user's own access token still works alongside the
    # impersonation token — neither path locks the other out.
    me_self = await client.get("/api/v1/auth/me", headers=user_headers)
    assert me_self.status_code == 200

    # Impersonation tokens survive a force-logout of the target user.
    # This is intentional: admin investigation should not be killed by
    # an admin's own force-logout against the same user.
    fl = await client.post(
        f"/api/v1/admin/users/{user_id}/force-logout",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert fl.status_code == 200
    me_after = await client.get(
        "/api/v1/auth/me", headers=impersonation_headers
    )
    assert me_after.status_code == 200
    # The user's own token, by contrast, IS killed.
    me_self_after = await client.get("/api/v1/auth/me", headers=user_headers)
    assert me_self_after.status_code == 401




# ── P11-I4 / Task 4 — usage analytics ────────────────────────────────────────
async def test_usage_analytics_returns_expected_shape(
    client: AsyncClient, admin_key
):
    """The response carries every documented field. Numeric counters
    are integers (not strings), top_apps_by_writes is a list of
    {app_id, write_count} dicts, users_by_plan is a flat dict."""
    r = await client.get(
        "/api/v1/admin/analytics/usage",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    expected_keys = {
        "generated_at",
        "active_users_last_30d",
        "total_writes_today",
        "total_reads_today",
        "total_writes_this_month",
        "total_reads_this_month",
        "top_apps_by_writes",
        "celery_queue_depth",
        "users_by_plan",
        "deletion_requests_pending",
    }
    assert expected_keys.issubset(body.keys()), (
        f"missing keys: {expected_keys - body.keys()}"
    )

    # Type contracts.
    assert isinstance(body["generated_at"], str)
    for k in (
        "active_users_last_30d",
        "total_writes_today",
        "total_reads_today",
        "total_writes_this_month",
        "total_reads_this_month",
        "deletion_requests_pending",
    ):
        assert isinstance(body[k], int), f"{k} must be int"
    assert isinstance(body["top_apps_by_writes"], list)
    for row in body["top_apps_by_writes"]:
        assert {"app_id", "write_count"}.issubset(row.keys())
        assert isinstance(row["write_count"], int)
    assert isinstance(body["users_by_plan"], dict)
    # celery_queue_depth is either an int or the string "unavailable"
    assert isinstance(body["celery_queue_depth"], (int, str))

    # The endpoint is gated by admin auth — no key → 401, wrong key → 403,
    # neither response carries the analytics fields.
    no_auth = await client.get("/api/v1/admin/analytics/usage")
    assert no_auth.status_code == 401
    assert "active_users_last_30d" not in no_auth.json()


async def test_usage_analytics_handles_redis_unavailable(
    client: AsyncClient, admin_key, monkeypatch
):
    """Redis being down must NOT 500 the analytics endpoint. The
    queue depth comes back as the literal string ``"unavailable"``
    while every other field still renders. This matches the R-301
    fail-open posture documented in BACKEND_RISKS.md."""
    from app.routers import admin as admin_module

    def _boom() -> str:
        # Match the production helper's contract on Redis failure.
        return "unavailable"

    monkeypatch.setattr(admin_module, "_celery_queue_depth", _boom)

    r = await client.get(
        "/api/v1/admin/analytics/usage",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["celery_queue_depth"] == "unavailable"
    # Every other counter still renders.
    assert isinstance(body["active_users_last_30d"], int)
    assert isinstance(body["total_writes_today"], int)
