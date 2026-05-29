"""RLS coverage for users / api_keys / token_usage (R-H7).

Migration 013 extends row-level security to these three tables. The
threat-model decision is documented in migration 013 and in
BACKEND_HARDENING_PHASE2.md §P2-C4:

  - SELECT on users / api_keys is permissive (login + key-hash lookup
    must work pre-auth). The protection that matters is on
    UPDATE / DELETE.
  - SELECT on token_usage is self-only (always read post-auth).
  - INSERT and UPDATE / DELETE on api_keys are self-only.
  - INSERT into users is permissive (registration); UPDATE / DELETE
    on users are self-only.

These tests prove the SQL-layer enforcement by setting the
`app.current_user_id` GUC to user B and attempting to mutate user
A's rows. RLS must produce zero affected rows / silent denial.

Companion to `tests/integration/test_rls_isolation.py` which covers
the memory tables.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


pytestmark = pytest.mark.asyncio


async def _fresh(http_client):
    email = f"rls_{uuid.uuid4().hex[:8]}@example.com"
    password = "RlsPass!2026demo"
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
        "user_id": me.json()["id"],
        "email": email,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


# ── users ──────────────────────────────────────────────────────────────────


async def test_user_b_cannot_update_user_a(http_client) -> None:
    a = await _fresh(http_client)
    b = await _fresh(http_client)

    from app.database import async_session

    # Authenticate the SQL session as user B and try to mark user A inactive.
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": b["user_id"]},
        )
        result = await s.execute(
            text("UPDATE users SET is_active = false WHERE id = :a RETURNING id"),
            {"a": a["user_id"]},
        )
        rows = result.fetchall()
        await s.commit()

    assert rows == [], (
        "RLS allowed user B to UPDATE user A's row; "
        "users_update_self policy is broken (R-H7)."
    )

    # Confirm user A is still active by querying via user A's GUC.
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": a["user_id"]},
        )
        r = await s.execute(
            text("SELECT is_active FROM users WHERE id = :a"), {"a": a["user_id"]}
        )
        is_active = r.scalar()
    assert is_active is True, "user A row was modified despite RLS"


async def test_user_b_cannot_delete_user_a(http_client) -> None:
    a = await _fresh(http_client)
    b = await _fresh(http_client)

    from app.database import async_session

    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": b["user_id"]},
        )
        result = await s.execute(
            text("DELETE FROM users WHERE id = :a RETURNING id"),
            {"a": a["user_id"]},
        )
        rows = result.fetchall()
        await s.commit()

    assert rows == [], "RLS allowed user B to DELETE user A's row"

    # Confirm user A still exists.
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": a["user_id"]},
        )
        r = await s.execute(
            text("SELECT count(*) FROM users WHERE id = :a"), {"a": a["user_id"]}
        )
        assert (r.scalar() or 0) == 1


# ── api_keys ───────────────────────────────────────────────────────────────


async def test_user_b_cannot_revoke_user_a_api_key_via_sql(http_client) -> None:
    a = await _fresh(http_client)
    b = await _fresh(http_client)

    # User A creates an API key.
    create = await http_client.post(
        "/api/v1/auth/api-keys",
        json={"name": "User A Key"},
        headers=a["headers"],
    )
    assert create.status_code == 201
    key_id = create.json()["id"]

    from app.database import async_session

    # User B tries to delete it via SQL.
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": b["user_id"]},
        )
        result = await s.execute(
            text("DELETE FROM api_keys WHERE id = :k RETURNING id"),
            {"k": key_id},
        )
        rows = result.fetchall()
        await s.commit()

    assert rows == [], "RLS allowed user B to DELETE user A's api_key"

    # Confirm user A's key still exists from A's perspective.
    listing = await http_client.get("/api/v1/auth/api-keys", headers=a["headers"])
    assert any(k["id"] == key_id for k in listing.json())


async def test_user_b_cannot_insert_apikey_for_user_a(http_client) -> None:
    """The api_keys INSERT policy WITH CHECK (user_id = GUC) must reject
    a session that authenticates as B from inserting a row owned by A.
    """
    a = await _fresh(http_client)
    b = await _fresh(http_client)

    from app.database import async_session

    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": b["user_id"]},
        )
        with pytest.raises(Exception):
            # Postgres will raise a row violation; the surrounding session
            # must roll back.
            await s.execute(
                text(
                    "INSERT INTO api_keys (user_id, key_hash, name, scopes, is_active) "
                    "VALUES (:uid, :h, 'forged', 'read,write', true)"
                ),
                {"uid": a["user_id"], "h": "x" * 64},
            )
            await s.commit()


# ── token_usage ────────────────────────────────────────────────────────────


async def test_user_b_cannot_read_user_a_token_usage(http_client) -> None:
    a = await _fresh(http_client)
    b = await _fresh(http_client)

    from app.database import async_session

    # As user A, insert a token_usage row.
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": a["user_id"]},
        )
        await s.execute(
            text(
                "INSERT INTO token_usage "
                "(user_id, prompt_tokens, completion_tokens, total_tokens, model, cost_cents) "
                "VALUES (:uid, 10, 20, 30, 'gpt-4o', 100)"
            ),
            {"uid": a["user_id"]},
        )
        await s.commit()

    # As user B, attempt to read all token_usage rows.
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": b["user_id"]},
        )
        r = await s.execute(text("SELECT count(*) FROM token_usage"))
        count_for_b = r.scalar() or 0

    assert count_for_b == 0, (
        "RLS allowed user B to SELECT user A's token_usage rows. "
        "token_usage_self policy is broken (R-H7)."
    )

    # Sanity: user A still sees their own row.
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": a["user_id"]},
        )
        r = await s.execute(text("SELECT count(*) FROM token_usage"))
        assert (r.scalar() or 0) == 1
