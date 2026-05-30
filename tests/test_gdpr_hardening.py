"""Phase 7 GDPR hardening tests (P7-E1, P7-E2).

* P7-E1: export route streams NDJSON, never buffers the full payload.
* P7-E2: delete route is atomic, requires confirmation, and wipes
  every user-scoped store.

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

    async def test_delete_with_confirm_wipes_everything(
        self, client: AsyncClient
    ):
        user_id, email, headers = await _make_user_with_data(client)
        # Sanity: stores have data before the delete.
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
        assert body["deleted"] is True
        assert body["authentication_invalidated"] is True
        counts = body["deleted_counts"]
        assert counts["episodic_memory"] >= 1
        assert counts["semantic_memory"] >= 1
        assert counts["procedural_memory"] == 1
        assert counts["knowledge_nodes"] >= 1
        assert counts["users"] == 1

        # Every store now empty for this user.
        assert demo_db.episodic_store.get(user_id, []) == []
        assert demo_db.semantic_store.get(user_id, []) == []
        assert demo_db.procedural_store.get(user_id) is None
        assert demo_db.graph_nodes_store.get(user_id, []) == []
        assert demo_auth.get_user_by_email(email) is None

    async def test_delete_rejects_other_user(self, client: AsyncClient):
        _, _, headers = await _make_user_with_data(client)
        someone_else = str(uuid.uuid4())
        r = await client.delete(
            f"/api/v1/memory/user/{someone_else}/all",
            headers={**headers, "X-Confirm-Delete": "true"},
        )
        assert r.status_code == 403

    async def test_delete_invalidates_session(self, client: AsyncClient):
        """After delete, the access token's user no longer exists in
        the demo store, so any authenticated route returns 401.

        This is the practical meaning of
        ``authentication_invalidated: true`` for the demo path.
        """
        user_id, _, headers = await _make_user_with_data(client)
        r = await client.delete(
            f"/api/v1/memory/user/{user_id}/all",
            headers={**headers, "X-Confirm-Delete": "true"},
        )
        assert r.status_code == 200

        # Same access token, post-delete: user is gone, /me 401s.
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 401

    async def test_delete_uses_explicit_transaction_in_db_path(self):
        """A reviewer reading the source must see the explicit
        ``async with db.begin():`` block so a future change cannot
        accidentally split the deletes across multiple transactions."""
        from app.routers.gdpr import delete_all_memories
        import inspect

        src = inspect.getsource(delete_all_memories)
        assert "async with db.begin()" in src


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
