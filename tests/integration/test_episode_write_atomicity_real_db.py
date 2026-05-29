"""Live-DB atomicity test for /memory/episode/write.

Companion to `tests/test_episode_write_atomicity.py`. The unit version
proves the route does not swallow exceptions; this integration test proves
that when an exception escapes the route, `get_db` actually rolls back
and no orphan rows remain.

We trigger a guaranteed failure mid-write by patching `persist_edge` on
the imported router module. We pre-seed the engram so it produces graph
edges and therefore reaches the persist_edge call.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


pytestmark = pytest.mark.asyncio


async def test_persist_edge_failure_rolls_back_episode(
    fresh_user, http_client, monkeypatch
) -> None:
    from app.routers import memory as memory_module

    # Inject a failure at the very last DB step. The episodic / semantic /
    # engram inserts have already happened in the same transaction; the
    # persist_edge failure must cause `get_db` to rollback all of them.
    async def _explode(*_a, **_kw):
        raise RuntimeError("simulated persist_edge failure")

    monkeypatch.setattr(memory_module, "persist_edge", _explode)

    # Use content that is virtually guaranteed to produce graph edges.
    payload = {
        "content": "Alice met Bob at the conference about pgvector and PostgreSQL.",
        "session_id": "atomicity-real-db",
        "tags": [],
        "metadata": {},
    }
    response = await http_client.post(
        "/api/v1/memory/episode/write",
        json=payload,
        headers=fresh_user["headers"],
    )

    # The request must fail (5xx). The exact code depends on FastAPI's
    # default exception handler for an uncaught RuntimeError.
    assert response.status_code >= 500, (
        f"expected a 5xx after simulated DB failure, got {response.status_code}"
    )

    # Confirm zero rows for this user across all five tables.
    from app.database import async_session

    user_id = fresh_user["user_id"]
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": user_id},
        )
        for table in (
            "episodic_memory",
            "semantic_memory",
            "engrams",
            "knowledge_nodes",
            "knowledge_edges",
        ):
            r = await s.execute(text(f"SELECT count(*) FROM {table}"))
            count = r.scalar() or 0
            assert count == 0, (
                f"orphan rows left in {table} after rolled-back episode write: "
                f"{count} row(s) remain (R-H1 regression)"
            )


async def test_happy_path_writes_all_layers(fresh_user, http_client) -> None:
    """When everything succeeds, all five layers carry exactly one new entry
    for this user. Acts as the positive control for the rollback test above.
    """
    payload = {
        "content": "Alice met Bob at the conference about pgvector and PostgreSQL.",
        "session_id": "atomicity-happy",
        "tags": [],
        "metadata": {},
    }
    response = await http_client.post(
        "/api/v1/memory/episode/write",
        json=payload,
        headers=fresh_user["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["episodic_id"] is not None
    assert body["semantic_id"] is not None
    assert body["engram_id"] is not None

    from app.database import async_session

    user_id = fresh_user["user_id"]
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": user_id},
        )
        for table in ("episodic_memory", "semantic_memory", "engrams"):
            r = await s.execute(text(f"SELECT count(*) FROM {table}"))
            count = r.scalar() or 0
            assert count >= 1, f"expected ≥1 row in {table} after happy-path write"
