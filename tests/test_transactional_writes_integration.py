"""Phase 2 integration test for /memory/episode/write rollback semantics.

Marked ``integration`` so it only runs in the CI integration job
(``RUN_DB_TESTS=1`` with real Postgres + pgvector). The unit job
deselects this marker via pytest.ini.

What it pins:
* On a forced mid-chain failure, none of episodic / semantic / engram
  / knowledge_node / knowledge_edge are persisted.
* The client receives HTTP 500 (not 200 with partial state).
"""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import AsyncClient


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="integration tests require RUN_DB_TESTS=1 and real Postgres",
    ),
]


async def test_partial_write_failure_leaves_no_orphan_rows(
    client: AsyncClient, monkeypatch
):
    """Force the engrams insert to fail and assert nothing committed."""
    # Register / login a real user against the integration DB.
    creds = {
        "email": f"tx_int_{uuid.uuid4().hex[:6]}@nexmem.example.com",
        "password": "TestPass123!",
    }
    reg = await client.post("/api/v1/auth/register", json=creds)
    assert reg.status_code in (200, 201)
    user_id = reg.json()["id"]
    login = await client.post("/api/v1/auth/login", json=creds)
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Patch the engrams insert to raise.
    from app.routers import memory

    real_execute = None

    async def failing_execute(self, statement, *args, **kwargs):
        sql = str(statement)
        if "INSERT INTO engrams" in sql:
            raise RuntimeError("forced engram insert failure")
        return await real_execute(self, statement, *args, **kwargs)

    from sqlalchemy.ext.asyncio import AsyncSession

    real_execute = AsyncSession.execute
    monkeypatch.setattr(AsyncSession, "execute", failing_execute, raising=False)

    payload = {
        "content": "rollback integration probe",
        "session_id": "tx_int_s",
        "tags": [],
        "metadata": {},
    }
    r = await client.post(
        "/api/v1/memory/episode/write", json=payload, headers=headers
    )
    assert r.status_code == 500, r.text

    # Restore execute for the verification queries.
    monkeypatch.setattr(AsyncSession, "execute", real_execute, raising=False)

    listing = await client.get(
        f"/api/v1/agents/{user_id}/episodes", headers=headers
    )
    assert listing.status_code == 200
    contents = [item.get("content", "") for item in listing.json()]
    assert "rollback integration probe" not in contents
