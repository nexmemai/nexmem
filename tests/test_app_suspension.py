"""Tests for app suspension (P4-B6, Block 7).

Covers the four spec'd behaviours:

* Suspended app: write routes 403.
* Suspended app: read routes still work.
* Unsuspend: write access restored on the next request.
* Suspend / unsuspend require X-Admin-Key.

All tests run in DEMO_MODE=true. The suspension state lives in
``demo_db.demo_apps_suspension`` and is wired in
``app.core.suspension_check``.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app import demo_db
from app.config import settings


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


ADMIN_KEY = "test-admin-key-must-be-at-least-32-chars-long"


@pytest.fixture
def admin_key(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", ADMIN_KEY)
    return ADMIN_KEY


@pytest.fixture(autouse=True)
def fast_ml(monkeypatch):
    """Replace the heavy ML calls with deterministic stubs.

    The /memory/episode/write demo branch calls ``embedder.embed``
    (sentence-transformers) and ``engram_processor.process_async``
    (spaCy). Both load model weights on first call, which adds ~30-60s
    to the first write in any test that exercises the route. The
    behaviour we care about in this file is the suspension dependency,
    not the NLP shape — so we stub both to fast no-ops.
    """
    from app.services import embedder as embedder_module
    from app.services import engram_processor as engram_module

    async def _fake_embed(_text: str):
        return embedder_module.embedder.random_vector()

    async def _fake_engram(text: str, user_id: str):
        return {
            "engram_id": "fake-engram-id",
            "distilled_text": text[:200],
            "compression_ratio": 1.0,
            "actions": [],
            "objects": [],
            "entities": [],
            "negated_actions": [],
            "salience_scores": {},
            "connections": [],
            "graph_edges": [],
            "dense_embedding": None,
            "original_length": len(text),
            "compressed_length": min(len(text), 200),
        }

    monkeypatch.setattr(embedder_module.embedder, "embed", _fake_embed)
    monkeypatch.setattr(
        engram_module.engram_processor, "process_async", _fake_engram
    )


@pytest.fixture
def suspended_app_request(monkeypatch):
    """Make every authenticated request behave as if it were bound
    to a suspended app.

    The suspension dependency reads ``request.state.current_app_id``,
    which is normally populated by the API-key auth path. Bearer
    tokens leave it None. To drive the suspended-app code path
    against bearer-token requests in tests, we monkeypatch the
    helper that the dependency uses to extract the app_id.
    """
    fake_app_id = str(uuid.uuid4())
    demo_db.demo_apps_suspension[fake_app_id] = {
        "suspended_at": "2026-05-23T00:00:00Z",
        "suspension_reason": "operator suspension test",
    }

    import app.core.suspension_check as sc

    monkeypatch.setattr(sc, "_request_app_id", lambda request: fake_app_id)
    return fake_app_id


# ── 1. Suspended app blocks writes ───────────────────────────────────────────
async def test_suspended_app_blocks_writes(
    client: AsyncClient, auth_headers, suspended_app_request
):
    """POST /memory/episode/write 403s with the documented detail."""
    r = await client.post(
        "/api/v1/memory/episode/write",
        json={
            "content": "test content",
            "session_id": "test-session",
        },
        headers=auth_headers,
    )
    assert r.status_code == 403, r.text
    body = r.json()
    detail = body.get("detail", body)
    assert detail.get("error") == "app_suspended"
    assert "suspended" in detail.get("message", "").lower()
    assert detail.get("app_id") == suspended_app_request


# ── 2. Suspended app still allows reads ──────────────────────────────────────
async def test_suspended_app_allows_reads(
    client: AsyncClient, auth_headers, suspended_app_request
):
    """The suspension dep is wired ONLY on write routes. Authenticated
    reads continue to flow — a suspended user must still be able to
    recover their own data.

    We use ``GET /api/v1/auth/me`` (a generic authenticated read) and
    ``GET /api/v1/apps/{app_id}/usage`` (a memory-domain read added
    in this same block). Neither route lists ``check_app_not_suspended``
    in its dependencies; the contract is that the suspended-app
    request reaches the handler and returns a 2xx body.

    We avoid ``POST /memory/context`` and ``POST /rag/chat`` here
    because they pay NLP / embedding costs that the sandbox does
    not provision; CI exercises those paths separately.
    """
    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert me.status_code == 200, me.text

    usage = await client.get(
        f"/api/v1/apps/{suspended_app_request}/usage",
        headers=auth_headers,
    )
    assert usage.status_code == 200, usage.text
    assert "usage" in usage.json()


# ── 3. Unsuspend restores write access ───────────────────────────────────────
async def test_unsuspend_restores_write_access(
    client: AsyncClient, auth_headers, admin_key, suspended_app_request
):
    """After the unsuspend route runs, the next write succeeds."""
    # Sanity: suspended → 403.
    r1 = await client.post(
        "/api/v1/memory/episode/write",
        json={"content": "blocked", "session_id": "s"},
        headers=auth_headers,
    )
    assert r1.status_code == 403

    # Operator unsuspends.
    r_admin = await client.post(
        f"/api/v1/admin/apps/{suspended_app_request}/unsuspend",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert r_admin.status_code == 200, r_admin.text
    body = r_admin.json()
    assert body["unsuspended"] is True
    assert body["app_id"] == suspended_app_request
    assert suspended_app_request not in demo_db.demo_apps_suspension

    # Next write succeeds.
    r2 = await client.post(
        "/api/v1/memory/episode/write",
        json={"content": "now allowed", "session_id": "s"},
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text


# ── 4. Suspend / unsuspend require admin key ─────────────────────────────────
async def test_suspension_requires_admin_key(client: AsyncClient, admin_key):
    """No header / wrong header / missing key all surface as the
    documented 401 / 403 / 501 trio. Each admin route is gated by
    ``get_admin_user`` so the contract is identical to existing
    admin endpoints."""
    fake_app_id = str(uuid.uuid4())

    # No header → 401.
    r1 = await client.post(
        f"/api/v1/admin/apps/{fake_app_id}/suspend",
        json={"reason": "bad behaviour"},
    )
    assert r1.status_code == 401

    # Wrong header → 403.
    r2 = await client.post(
        f"/api/v1/admin/apps/{fake_app_id}/suspend",
        json={"reason": "bad behaviour"},
        headers={"X-Admin-Key": "WRONG-KEY"},
    )
    assert r2.status_code == 403

    # Correct header → 200, suspension is recorded.
    r3 = await client.post(
        f"/api/v1/admin/apps/{fake_app_id}/suspend",
        json={"reason": "bad behaviour"},
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert r3.status_code == 200, r3.text
    body = r3.json()
    assert body["suspended"] is True
    assert body["app_id"] == fake_app_id
    assert "suspended_at" in body
    # Recorded in the demo store with the exact reason.
    rec = demo_db.demo_apps_suspension[fake_app_id]
    assert rec["suspension_reason"] == "bad behaviour"

    # Audit trail.
    from app.core.audit_log import list_demo_auth_events

    events = list_demo_auth_events(fake_app_id)
    actions = [e["action"] for e in events]
    assert "app_suspended" in actions


async def test_suspend_requires_reason_body(client: AsyncClient, admin_key):
    """Missing reason → 422. The reason is required for the audit row."""
    fake_app_id = str(uuid.uuid4())
    r = await client.post(
        f"/api/v1/admin/apps/{fake_app_id}/suspend",
        json={},
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert r.status_code == 422


async def test_suspension_dep_noop_when_no_app_context(
    client: AsyncClient, auth_headers
):
    """Without an app_id on request.state, the dependency is a no-op.

    Bearer-token (JWT) auth leaves ``current_app_id = None``. A
    suspended app for SOME OTHER caller must not block the JWT
    user's writes.
    """
    other_app_id = str(uuid.uuid4())
    demo_db.demo_apps_suspension[other_app_id] = {
        "suspended_at": "2026-05-23T00:00:00Z",
        "suspension_reason": "unrelated",
    }
    r = await client.post(
        "/api/v1/memory/episode/write",
        json={"content": "unrelated user", "session_id": "s"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
