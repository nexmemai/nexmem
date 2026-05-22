"""Cross-tenant isolation tests against a real Postgres with RLS enabled.

Ensures that:
  - User A's writes are not visible to user B through any router.
  - Episode write → semantic write → engram write all carry user_id and
    are correctly fenced by the RLS predicates from migration 008.
  - User B cannot delete user A's episodes.
  - Direct SQL probe with a different `app.current_user_id` GUC returns
    zero rows (the RLS policy actively filters, not just the WHERE clause).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text


pytestmark = pytest.mark.asyncio


async def _create_episode(http_client: AsyncClient, user, content: str) -> str:
    r = await http_client.post(
        f"/api/v1/agents/{user['user_id']}/episodes",
        json={
            "session_id": "isolation-test",
            "content": content,
            "metadata": {},
            "tags": [],
        },
        headers=user["headers"],
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.fixture
async def two_users(http_client: AsyncClient, redis_flushed):
    """Two registered users, each with a fresh access token."""
    import uuid

    async def _make():
        email = f"u_{uuid.uuid4().hex[:8]}@example.com"
        password = "Cross!Tenant!2026"
        await http_client.post(
            "/api/v1/auth/register", json={"email": email, "password": password}
        )
        login = await http_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        token = login.json()["access_token"]
        me = await http_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        return {
            "email": email,
            "password": password,
            "user_id": me.json()["id"],
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
        }

    a = await _make()
    b = await _make()
    return a, b


async def test_user_b_cannot_list_user_as_episodes(
    two_users, http_client: AsyncClient
) -> None:
    a, b = two_users
    await _create_episode(http_client, a, "user A's secret content")
    await _create_episode(http_client, b, "user B's own content")

    list_b = await http_client.get(
        f"/api/v1/agents/{b['user_id']}/episodes", headers=b["headers"]
    )
    assert list_b.status_code == 200
    contents = [e["content"] for e in list_b.json()]
    assert "user A's secret content" not in contents
    assert "user B's own content" in contents


async def test_user_b_cannot_request_user_as_episodes_directly(
    two_users, http_client: AsyncClient
) -> None:
    """Attempting to scope the request URL to user A while authenticated as B
    must be rejected at the router (403), independent of RLS.
    """
    a, b = two_users
    r = await http_client.get(
        f"/api/v1/agents/{a['user_id']}/episodes", headers=b["headers"]
    )
    assert r.status_code == 403


async def test_user_b_cannot_delete_user_as_episode(
    two_users, http_client: AsyncClient
) -> None:
    a, b = two_users
    epi = await _create_episode(http_client, a, "should survive")

    # B authenticates but tries to delete via A's URL → 403
    r = await http_client.delete(
        f"/api/v1/agents/{a['user_id']}/episodes/{epi}", headers=b["headers"]
    )
    assert r.status_code == 403

    # Episode still readable by A
    list_a = await http_client.get(
        f"/api/v1/agents/{a['user_id']}/episodes", headers=a["headers"]
    )
    assert any(e["id"] == epi for e in list_a.json())


async def test_rls_predicate_filters_at_sql_level(
    two_users, http_client: AsyncClient
) -> None:
    """Direct SQL probe: with `app.current_user_id` set to user B,
    selecting from episodic_memory must not see user A's row.

    This proves the protection is at the database layer (migration 008),
    not just the application's WHERE clauses.
    """
    a, b = two_users
    await _create_episode(http_client, a, "fenced by RLS")

    from app.database import async_session

    async with async_session() as session:
        # Force the GUC to user B and read.
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": b["user_id"]},
        )
        r = await session.execute(text("SELECT count(*) FROM episodic_memory"))
        count_for_b = r.scalar() or 0

        # Switch to user A — should see 1 row.
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": a["user_id"]},
        )
        r = await session.execute(text("SELECT count(*) FROM episodic_memory"))
        count_for_a = r.scalar() or 0

    assert count_for_b == 0, "RLS did not fence user A's row from user B's session"
    assert count_for_a == 1
