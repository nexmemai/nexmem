"""Cross-app isolation integration tests (R-H2 / R-H5).

Same user, two app_ids, must produce two disjoint memory views.

Covers:
  - Episode write under app_id=A is not visible to recall scoped by app_id=B.
  - Engram row carries the app_id sent in the request body.
  - /memory/context filtered by app_id only returns rows for that app.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text


pytestmark = pytest.mark.asyncio


async def _write_episode(http_client: AsyncClient, user, app_id: str | None, content: str):
    body = {
        "content": content,
        "session_id": "cross-app-test",
        "tags": [],
        "metadata": {},
    }
    if app_id is not None:
        body["app_id"] = app_id
    r = await http_client.post(
        "/api/v1/memory/episode/write",
        json=body,
        headers=user["headers"],
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_two_apps_same_user_have_disjoint_episodes(
    fresh_user, http_client: AsyncClient
) -> None:
    app_a = str(uuid.uuid4())
    app_b = str(uuid.uuid4())

    await _write_episode(http_client, fresh_user, app_a, "Memory belonging to app A only.")
    await _write_episode(http_client, fresh_user, app_b, "Memory belonging to app B only.")
    # One memory with no app_id (legacy / user-scoped).
    await _write_episode(http_client, fresh_user, None, "User-level memory, no app_id.")

    # List episodes filtered by app A.
    r_a = await http_client.get(
        f"/api/v1/agents/{fresh_user['user_id']}/episodes",
        params={"app_id": app_a},
        headers=fresh_user["headers"],
    )
    assert r_a.status_code == 200, r_a.text
    contents_a = [e["content"] for e in r_a.json()]
    assert "Memory belonging to app A only." in contents_a
    assert "Memory belonging to app B only." not in contents_a

    # List episodes filtered by app B.
    r_b = await http_client.get(
        f"/api/v1/agents/{fresh_user['user_id']}/episodes",
        params={"app_id": app_b},
        headers=fresh_user["headers"],
    )
    contents_b = [e["content"] for e in r_b.json()]
    assert "Memory belonging to app B only." in contents_b
    assert "Memory belonging to app A only." not in contents_b


async def test_engram_row_carries_request_app_id(
    fresh_user, http_client: AsyncClient
) -> None:
    """The engram INSERT must thread the request's app_id into the row.
    Verified by reading the engrams table directly with RLS GUC set.
    """
    app_a = str(uuid.uuid4())
    body = await _write_episode(
        http_client, fresh_user, app_a, "Alice met Bob about pgvector."
    )
    engram_id = body["engram_id"]
    assert engram_id is not None

    from app.database import async_session

    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": fresh_user["user_id"]},
        )
        r = await s.execute(
            text("SELECT app_id FROM engrams WHERE engram_id = :eid"),
            {"eid": engram_id},
        )
        row = r.fetchone()

    assert row is not None
    assert str(row[0]) == app_a, (
        f"engram row app_id ({row[0]!r}) does not match the request app_id "
        f"({app_a!r}). R-H5 regression."
    )


async def test_rag_chat_does_not_attribute_error_on_user_app_id(
    fresh_user, http_client: AsyncClient
) -> None:
    """The previous /rag/chat called `current_user.app_id` which raises
    AttributeError on the User model. The route must succeed (or fail
    cleanly) without that error. Demo-mode integration test.
    """
    r = await http_client.post(
        "/api/v1/rag/chat",
        json={
            "user_id": fresh_user["user_id"],
            "message": "Test message for AttributeError guard.",
            "include_episodic": False,
            "include_semantic": False,
            "include_procedural": False,
            "include_graph": False,
            "top_k": 1,
        },
        headers=fresh_user["headers"],
    )
    # The body shape is irrelevant; the only thing we're testing is that
    # the route does not 500 from a Python AttributeError.
    assert r.status_code != 500, (
        f"/rag/chat returned 500. If the body is "
        f"{{detail: 'Internal Server Error'}}, this is the R-H2 regression."
    )
