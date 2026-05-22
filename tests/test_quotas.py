"""Phase 2 unit tests for quota enforcement.

R-005 / R-108: enforce_write_quota and enforce_read_quota are wired
into every write route plus /memory/context and /rag/chat. These
tests pin:

* Demo mode is a no-op (the test infrastructure runs in demo mode).
* The cap functions return the configured values per tier.
* Redis incr+expire is called once per request through the
  dependency, with a fail-closed 503 if Redis raises.
* A user past their cap gets HTTP 429 with a structured payload.
* The dependency is wired into every write route (introspection
  test against the live FastAPI app).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import settings
from app.core import quotas
from app.models.user import User
from app.main import app as fastapi_app


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _make_user(tier: str = "free", uid: str = "00000000-0000-0000-0000-000000000abc") -> User:
    """Construct a User-shaped object the quota helpers can read."""
    import uuid as _uuid
    from datetime import datetime
    user = User(
        id=_uuid.UUID(uid),
        is_active=True,
        created_at=datetime.utcnow(),
    )
    user.tier = tier
    return user


# ── Cap tables ──────────────────────────────────────────────────────────────
def test_write_caps_per_tier():
    assert quotas._write_cap_for("free") == settings.free_monthly_writes
    assert quotas._write_cap_for("starter") == settings.starter_monthly_writes
    assert quotas._write_cap_for("pro") == settings.pro_monthly_writes
    assert quotas._write_cap_for("enterprise") == quotas._INFINITE
    assert quotas._write_cap_for("nonsense") == settings.free_monthly_writes


def test_read_caps_per_tier():
    assert quotas._read_cap_for("free") == settings.free_monthly_reads
    assert quotas._read_cap_for("starter") == settings.starter_monthly_reads
    assert quotas._read_cap_for("pro") == settings.pro_monthly_reads
    assert quotas._read_cap_for("enterprise") == quotas._INFINITE


# ── Dependency behaviour ────────────────────────────────────────────────────
async def test_enforce_write_quota_is_noop_in_demo(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    user = _make_user()

    class _Req:
        pass

    # Should not raise even though Redis is not configured.
    await quotas.enforce_write_quota(_Req(), user)


async def test_check_and_increment_returns_zero_when_no_redis(monkeypatch):
    """If REDIS_URL is unset, we don't raise (we log and let through).

    Production has REDIS_URL; the no-Redis path is for local dev and
    is documented in BACKEND_RISKS as a P1 risk in non-Redis envs.
    """
    monkeypatch.setattr(settings, "redis_url", None)
    user = _make_user()
    out = await quotas._check_and_increment(user, "write", 1000)
    assert out == 0


async def test_check_and_increment_raises_503_on_redis_error(monkeypatch):
    """When Redis is configured but unreachable, fail closed."""
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "demo_mode", False)

    class FakeClient:
        async def incr(self, key):
            raise RuntimeError("redis exploded")

        async def aclose(self):
            return None

    monkeypatch.setattr(quotas, "_redis_client", lambda: FakeClient())

    user = _make_user()
    with pytest.raises(HTTPException) as exc:
        await quotas._check_and_increment(user, "write", 1000)
    assert exc.value.status_code == 503


async def test_check_and_increment_raises_429_when_cap_exceeded(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "demo_mode", False)

    class FakeClient:
        def __init__(self):
            self.incr_value = 5  # one above cap of 4
            self.expire_called = False

        async def incr(self, key):
            return self.incr_value

        async def expire(self, key, seconds):
            self.expire_called = True
            return True

        async def aclose(self):
            return None

    fake = FakeClient()
    monkeypatch.setattr(quotas, "_redis_client", lambda: fake)

    user = _make_user()
    with pytest.raises(HTTPException) as exc:
        await quotas._check_and_increment(user, "write", 4)
    assert exc.value.status_code == 429
    assert exc.value.detail["kind"] == "write"
    assert exc.value.detail["quota"] == 4


async def test_check_and_increment_calls_expire_on_first_increment(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "demo_mode", False)

    class FakeClient:
        def __init__(self):
            self.expire_calls = []

        async def incr(self, key):
            return 1  # first call

        async def expire(self, key, seconds):
            self.expire_calls.append((key, seconds))
            return True

        async def aclose(self):
            return None

    fake = FakeClient()
    monkeypatch.setattr(quotas, "_redis_client", lambda: fake)

    user = _make_user()
    out = await quotas._check_and_increment(user, "write", 100)
    assert out == 1
    assert len(fake.expire_calls) == 1
    key, seconds = fake.expire_calls[0]
    assert key.startswith("quota:write:")
    assert seconds > 0  # bounded to end of month


async def test_check_and_increment_skips_expire_after_first(monkeypatch):
    """Subsequent INCs must not reset the TTL."""
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "demo_mode", False)

    class FakeClient:
        def __init__(self):
            self.expire_calls = []

        async def incr(self, key):
            return 7  # not first

        async def expire(self, key, seconds):
            self.expire_calls.append(seconds)
            return True

        async def aclose(self):
            return None

    fake = FakeClient()
    monkeypatch.setattr(quotas, "_redis_client", lambda: fake)

    user = _make_user()
    await quotas._check_and_increment(user, "write", 100)
    assert fake.expire_calls == []


async def test_enterprise_tier_skips_redis(monkeypatch):
    """Enterprise has no cap so we don't burn Redis budget."""
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "demo_mode", False)

    incr_called = []

    class FakeClient:
        async def incr(self, key):
            incr_called.append(key)
            return 1

        async def expire(self, key, seconds):
            return True

        async def aclose(self):
            return None

    monkeypatch.setattr(quotas, "_redis_client", lambda: FakeClient())

    user = _make_user(tier="enterprise")
    out = await quotas._check_and_increment(user, "write", quotas._INFINITE)
    assert out == 0
    assert incr_called == []


# ── Wiring introspection: every write route declares the dep ────────────────
def test_quota_dep_is_wired_into_write_routes():
    """The four write routes plus /memory/episode/write must declare
    the enforce_write_quota dependency."""
    expected_paths = {
        "/api/v1/agents/{user_id}/episodes",
        "/api/v1/agents/{user_id}/semantics",
        "/api/v1/agents/{user_id}/procedural/settings",
        "/api/v1/agents/{user_id}/graph/nodes",
        "/api/v1/agents/{user_id}/graph/edges",
        "/api/v1/memory/episode/write",
    }
    found = set()
    for route in fastapi_app.routes:
        if not hasattr(route, "endpoint") or "POST" not in getattr(route, "methods", set()):
            continue
        deps = getattr(route, "dependant", None)
        if deps is None:
            continue
        # Walk the dependant tree looking for our function.
        names = []
        stack = [deps]
        while stack:
            d = stack.pop()
            if d.call is not None:
                names.append(getattr(d.call, "__name__", ""))
            stack.extend(d.dependencies)
        if "enforce_write_quota" in names and route.path in expected_paths:
            found.add(route.path)
    missing = expected_paths - found
    assert not missing, f"enforce_write_quota missing on: {missing}"


def test_quota_dep_is_wired_into_read_routes():
    expected_paths = {
        "/api/v1/memory/context",
        "/api/v1/rag/chat",
    }
    found = set()
    for route in fastapi_app.routes:
        if not hasattr(route, "endpoint") or "POST" not in getattr(route, "methods", set()):
            continue
        deps = getattr(route, "dependant", None)
        if deps is None:
            continue
        names = []
        stack = [deps]
        while stack:
            d = stack.pop()
            if d.call is not None:
                names.append(getattr(d.call, "__name__", ""))
            stack.extend(d.dependencies)
        if "enforce_read_quota" in names and route.path in expected_paths:
            found.add(route.path)
    missing = expected_paths - found
    assert not missing, f"enforce_read_quota missing on: {missing}"
