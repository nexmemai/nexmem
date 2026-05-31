"""Phase 7 GDPR hardening tests (P7-E1, P7-E2 / Block 5 P7-E4).

* P7-E1: export route streams NDJSON, never buffers the full payload.
* P7-E2 → P7-E4 (Block 5): delete route requires confirmation,
  schedules a soft-delete with a 30-day grace period, and freezes
  the account immediately. The actual cascade-delete now runs in
  the ``execute_scheduled_deletions`` Celery task and is covered
  by ``tests/test_gdpr_soft_delete.py``.

Runs in DEMO_MODE so no Postgres is required.
"""
from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient

from app import demo_db
from app.core import demo_auth


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# ── helpers ──────────────────────────────────────────────────────────────────
async def _make_user_with_data(client: AsyncClient) -> tuple[str, str, dict]:
    """Register, log in, and seed the demo stores with one row of each kind."""
    email = f"gdpr_{uuid.uuid4().hex[:8]}@nexmem.example.com"
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

    # Seed one record of each kind directly into the demo stores so
    # we don't need every write route to exist for the test.
    demo_db.create_episodic(
        user_id, "session_x", "An episode for GDPR testing", tags=["g"]
    )
    demo_db.create_semantic(
        user_id, vector=[0.1] * 384, summary="semantic", content_preview="s"
    )
    demo_db.upsert_procedural(user_id, settings={"theme": "light"}, workflows=[])
    demo_db.create_node(user_id, label="A", node_type="concept")
    return user_id, email, headers


# ── P7-E1: streaming export ──────────────────────────────────────────────────
class TestStreamingExport:
    async def test_export_returns_ndjson(
        self, client: AsyncClient
    ):
        user_id, _, headers = await _make_user_with_data(client)

        async with client.stream(
            "GET",
            f"/api/v1/memory/user/{user_id}/export",
            headers=headers,
        ) as resp:
            assert resp.status_code == 200, await resp.aread()
            assert resp.headers["content-type"] == "application/x-ndjson"
            assert "attachment" in resp.headers.get("content-disposition", "")
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)

        body = b"".join(chunks).decode()
        lines = [line for line in body.splitlines() if line.strip()]
        # Every line is valid JSON.
        parsed = [json.loads(line) for line in lines]
        # First line is metadata, remaining lines are records.
        assert parsed[0]["kind"] == "metadata"
        assert parsed[0]["user_id"] == user_id
        assert parsed[0]["format"] == "ndjson"

        kinds = {p["kind"] for p in parsed[1:]}
        assert {"episodic", "semantic", "procedural", "knowledge_node"}.issubset(
            kinds
        )

    async def test_export_rejects_other_user(self, client: AsyncClient):
        _, _, headers = await _make_user_with_data(client)
        someone_else = str(uuid.uuid4())
        r = await client.get(
            f"/api/v1/memory/user/{someone_else}/export",
            headers=headers,
        )
        assert r.status_code == 403

    async def test_export_uses_streaming_response(self):
        """The route handler must return StreamingResponse, not a buffered
        dict — otherwise the OOM regression sneaks back in."""
        from app.routers.gdpr import export_all_memories
        import inspect

        src = inspect.getsource(export_all_memories)
        # It should construct a StreamingResponse and never assemble
        # a full dict result like the old version did.
        assert "StreamingResponse" in src
        assert "return {" not in src or "return StreamingResponse" in src


# ── P7-E2: atomic delete ─────────────────────────────────────────────────────
class TestAtomicDelete:
    async def test_delete_requires_confirm_header(self, client: AsyncClient):
        user_id, _, headers = await _make_user_with_data(client)
        r = await client.delete(
            f"/api/v1/memory/user/{user_id}/all", headers=headers
        )
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"].lower()

    async def test_delete_with_confirm_schedules_soft_delete(
        self, client: AsyncClient
    ):
        """P7-E4 (Block 5): DELETE no longer wipes immediately —
        it stamps deletion_scheduled_for and returns the schedule
        envelope. Memory rows stay until the Celery task runs."""
        user_id, email, headers = await _make_user_with_data(client)
        # Sanity: stores have data before the request.
        assert demo_db.episodic_store.get(user_id)
        assert demo_db.semantic_store.get(user_id)
        assert demo_db.procedural_store.get(user_id)
        assert demo_db.graph_nodes_store.get(user_id)
        assert demo_auth.get_user_by_email(email) is not None

        r = await client.delete(
            f"/api/v1/memory/user/{user_id}/all",
            headers={**headers, "X-Confirm-Delete": "true"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # New contract.
        assert body["scheduled_deletion"] is True
        assert body["grace_period_days"] == 30
        assert body["deletion_date"]
        assert body["cancel_before"] == body["deletion_date"]
        assert body["cancel_url"].endswith(
            f"/memory/user/{user_id}/cancel-deletion"
        )
        # Old contract MUST NOT leak — a client that still expected
        # ``deleted: true`` would now miss the soft-delete signal.
        assert "deleted" not in body
        assert "deleted_counts" not in body

        # Memory stores are unchanged — no immediate cascade.
        assert demo_db.episodic_store.get(user_id)
        assert demo_db.semantic_store.get(user_id)
        assert demo_db.graph_nodes_store.get(user_id)

        # User row still exists, but is now inactive.
        still = demo_auth.get_user_by_email(email)
        assert still is not None
        assert still.is_active is False
        assert still.deletion_scheduled_for is not None

    async def test_delete_rejects_other_user(self, client: AsyncClient):
        _, _, headers = await _make_user_with_data(client)
        someone_else = str(uuid.uuid4())
        r = await client.delete(
            f"/api/v1/memory/user/{someone_else}/all",
            headers={**headers, "X-Confirm-Delete": "true"},
        )
        assert r.status_code == 403

    async def test_delete_invalidates_session(self, client: AsyncClient):
        """After soft-delete, the access token's user is now is_active=False
        in the demo store, so authenticated routes return 401.

        This is the practical meaning of the freeze part of P7-E4 —
        even though the row + memories survive the grace period,
        the user cannot keep using the API in the meantime.
        """
        user_id, _, headers = await _make_user_with_data(client)
        r = await client.delete(
            f"/api/v1/memory/user/{user_id}/all",
            headers={**headers, "X-Confirm-Delete": "true"},
        )
        assert r.status_code == 200

        # Same access token, post-soft-delete: user is now inactive,
        # so /me 401s.
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 401

    async def test_delete_uses_explicit_transaction_in_db_path(self):
        """A reviewer reading the source must see the explicit
        ``async with db.begin():`` block on the production soft-delete
        UPDATE so a future change cannot accidentally split the
        ``deletion_scheduled_for`` write from the ``is_active`` flip."""
        from app.routers.gdpr import delete_all_memories
        import inspect

        src = inspect.getsource(delete_all_memories)
        assert "async with db.begin()" in src
        # And the route must update the soft-delete columns, not
        # delete rows — that's the P7-E4 invariant.
        assert "deletion_scheduled_for" in src
        assert "is_active=False" in src


# ── consent update ───────────────────────────────────────────────────────────
class TestConsent:
    async def test_consent_demo_update(self, client: AsyncClient):
        user_id, _, headers = await _make_user_with_data(client)
        r = await client.patch(
            f"/api/v1/memory/user/{user_id}/consent",
            json={"marketing": True, "analytics": False},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["consent"] == {"marketing": True, "analytics": False}
        # Persisted in demo store.
        proc = demo_db.procedural_store[user_id]
        assert proc["settings"]["consent"] == {
            "marketing": True,
            "analytics": False,
        }
