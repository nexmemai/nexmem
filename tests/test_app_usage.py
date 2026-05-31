"""Tests for per-app monthly usage tracking (P4-B5, Block 7).

Covers the four spec'd behaviours:

1. ``increment_app_write`` upserts the demo store correctly.
2. ``increment_app_read`` does the same for the read counter.
3. ``GET /api/v1/apps/{app_id}/usage`` returns the documented shape.
4. ``record_app_write`` swallows every exception — the spec
   guarantees the increment never blocks the response or causes
   a rollback. Logged at WARNING; never raises.

The endpoint ownership-check is exercised via a production-mode
mock: in DEMO_MODE the endpoint intentionally skips ownership
(no apps table), so we patch ``settings.demo_mode`` and stub the
DB lookup to drive the production branch.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app import demo_db
from app.config import settings
from app.services.app_quota import (
    _month_year,
    get_app_usage,
    increment_app_read,
    increment_app_write,
    record_app_write,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _fixed_app_user() -> tuple[str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4())


# ── 1. increment_app_write ───────────────────────────────────────────────────
async def test_increment_write_creates_usage_record():
    """First call seeds the row at write_count=1; second increments to 2."""
    app_id, user_id = _fixed_app_user()
    month = _month_year()
    key = (app_id, month)
    assert key not in demo_db.demo_app_usage

    # The signature accepts a db arg for parity with the production
    # path. In demo mode it is unused — we pass None.
    await increment_app_write(None, app_id, user_id)
    rec = demo_db.demo_app_usage[key]
    assert rec["write_count"] == 1
    assert rec["read_count"] == 0
    assert rec["month_year"] == month
    assert rec["app_id"] == app_id
    assert rec["user_id"] == user_id

    await increment_app_write(None, app_id, user_id)
    assert demo_db.demo_app_usage[key]["write_count"] == 2
    # Read counter stays untouched.
    assert demo_db.demo_app_usage[key]["read_count"] == 0


# ── 2. increment_app_read ────────────────────────────────────────────────────
async def test_increment_read_creates_usage_record():
    """Independent counter from writes; both can coexist on one row."""
    app_id, user_id = _fixed_app_user()
    month = _month_year()
    key = (app_id, month)

    await increment_app_read(None, app_id, user_id)
    await increment_app_read(None, app_id, user_id)
    await increment_app_write(None, app_id, user_id)
    rec = demo_db.demo_app_usage[key]
    assert rec["read_count"] == 2
    assert rec["write_count"] == 1


# ── 3. get_app_usage → monthly breakdown ─────────────────────────────────────
async def test_get_usage_returns_monthly_breakdown():
    """Multiple months for the same app come back newest-first.

    We populate the demo store directly to bypass the wall-clock-
    only ``_month_year`` helper (we cannot rewind time inside one
    test run).
    """
    app_id, user_id = _fixed_app_user()
    months = ("2026-03", "2026-04", "2026-05")
    for i, m in enumerate(months):
        demo_db.demo_app_usage[(app_id, m)] = {
            "app_id": app_id,
            "user_id": user_id,
            "month_year": m,
            "write_count": (i + 1) * 10,
            "read_count": (i + 1) * 100,
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }

    rows = await get_app_usage(None, app_id, months=4)
    # Newest first.
    assert [r["month_year"] for r in rows] == ["2026-05", "2026-04", "2026-03"]
    assert rows[0]["write_count"] == 30
    assert rows[0]["read_count"] == 300
    # Each row carries the documented fields.
    expected_keys = {
        "app_id",
        "month_year",
        "write_count",
        "read_count",
        "last_updated",
    }
    for r in rows:
        assert expected_keys.issubset(r.keys())

    # months=2 caps the result.
    capped = await get_app_usage(None, app_id, months=2)
    assert len(capped) == 2
    assert [r["month_year"] for r in capped] == ["2026-05", "2026-04"]


# ── 4. /api/v1/apps/{app_id}/usage ownership ─────────────────────────────────
async def test_usage_endpoint_requires_app_ownership(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Production path: a 404 for an app the caller does not own.

    Demo mode bypasses ownership (no apps table). We monkeypatch
    ``settings.demo_mode = False`` and stub the DB session so the
    endpoint exercises the production branch end-to-end.
    """
    foreign_app_id = str(uuid.uuid4())

    # Build a fake App row owned by SOME OTHER user.
    foreign_owner_id = uuid.uuid4()
    fake_app = MagicMock()
    fake_app.id = uuid.UUID(foreign_app_id)
    fake_app.user_id = foreign_owner_id

    async def _fake_execute(*args, **kwargs):
        # The endpoint runs SELECT App WHERE id=...; we don't care
        # about the statement, only the return shape.
        result = MagicMock()
        result.scalar_one_or_none.return_value = fake_app
        return result

    monkeypatch.setattr(settings, "demo_mode", False)

    with patch("app.routers.apps.get_db") as _get_db_dep:
        # FastAPI will inject whatever this dependency yields; we
        # bypass that by overriding the router-level dependency.
        from app.main import app as fastapi_app
        from app.database import get_db

        async def _fake_db():
            db = MagicMock()
            db.execute = AsyncMock(side_effect=_fake_execute)
            yield db

        fastapi_app.dependency_overrides[get_db] = _fake_db
        try:
            r = await client.get(
                f"/api/v1/apps/{foreign_app_id}/usage",
                headers=auth_headers,
            )
        finally:
            fastapi_app.dependency_overrides.pop(get_db, None)

    # Foreign app → 404 (NOT 403; see comment in the route).
    assert r.status_code == 404, r.text


async def test_usage_endpoint_returns_demo_fixture_in_demo_mode(
    client: AsyncClient, auth_headers: dict
):
    """Demo mode short-circuit: any UUID returns the documented fixture
    when no usage rows exist."""
    app_id = str(uuid.uuid4())
    r = await client.get(
        f"/api/v1/apps/{app_id}/usage",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["app_id"] == app_id
    assert isinstance(body["usage"], list)
    assert len(body["usage"]) >= 1
    first = body["usage"][0]
    assert {"month_year", "write_count", "read_count"}.issubset(first.keys())


async def test_usage_endpoint_reflects_recorded_increments(
    client: AsyncClient, auth_headers: dict
):
    """Once writes / reads have been recorded, the endpoint returns
    the actual counters instead of the static fallback fixture."""
    app_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    # Seed three increments.
    await increment_app_write(None, app_id, user_id)
    await increment_app_write(None, app_id, user_id)
    await increment_app_read(None, app_id, user_id)

    r = await client.get(
        f"/api/v1/apps/{app_id}/usage",
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = r.json()["usage"]
    current = next(
        (rr for rr in rows if rr["month_year"] == _month_year()),
        None,
    )
    assert current is not None
    assert current["write_count"] == 2
    assert current["read_count"] == 1


# ── 5. Fire-and-forget never raises ──────────────────────────────────────────
async def test_increment_failure_does_not_block_response(monkeypatch, caplog):
    """``record_app_write`` must swallow every exception.

    Two failure modes are exercised:

    * Demo path: ``_demo_increment`` raises (covered by the outer
      try/except in ``record_app_write`` — added in Block 7 to
      make the demo path equally safe).
    * Production path: ``async_session`` itself raises before the
      session can be opened.

    In both cases the helper must return normally and emit a
    WARNING-level log line. This is the core spec contract: an
    app-metrics failure can never poison a successful write.
    """
    import logging

    import app.services.app_quota as mod

    app_id, user_id = _fixed_app_user()

    # ── Demo path ───────────────────────────────────────────────────
    def _boom_demo(app_id, user_id, *, is_write):
        raise RuntimeError("simulated metrics failure (demo)")

    monkeypatch.setattr(mod, "_demo_increment", _boom_demo)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.services.app_quota"):
        # Must NOT raise.
        await mod.record_app_write(app_id, user_id)
    assert any(
        "write increment failed" in rec.message
        for rec in caplog.records
    ), [r.message for r in caplog.records]

    # ── Production path ─────────────────────────────────────────────
    monkeypatch.setattr(settings, "demo_mode", False)

    def _boom_session(*a, **k):
        raise RuntimeError("simulated session failure (prod)")

    import app.database

    monkeypatch.setattr(app.database, "async_session", _boom_session)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.services.app_quota"):
        # Must NOT raise.
        await mod.record_app_write(app_id, user_id)
    assert any(
        "write increment failed" in rec.message
        for rec in caplog.records
    ), [r.message for r in caplog.records]
