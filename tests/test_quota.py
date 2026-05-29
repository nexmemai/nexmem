"""Tests for app.core.quota — per-user monthly write quota.

These tests exercise the dependency directly (no FastAPI routes) using a
fakeredis async client, so they run without an external Redis service.

Coverage:
- Tier limit lookup (free / starter / pro / enterprise / unknown).
- Demo mode is a no-op.
- No REDIS_URL configured → fail-open (no enforcement).
- Under quota: increments and returns the user.
- At quota: last permitted write succeeds, the next is rejected with 429.
- Over quota: structured 429 payload.
- Enterprise tier: bypasses entirely.
- Redis unreachable while configured: fails CLOSED with 503.
- TTL helpers: month-end calculation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import fakeredis.aioredis
import pytest

from app.core import quota as quota_mod
from app.core.quota import (
    QuotaExceeded,
    _quota_key,
    _seconds_until_month_end_utc,
    _tier_limit,
    enforce_write_quota,
)
from fastapi import HTTPException


def _user(*, tier: str = "free") -> SimpleNamespace:
    """Stand-in for the User model. enforce_write_quota only needs `id` and `tier`."""
    return SimpleNamespace(id=uuid.uuid4(), tier=tier, is_active=True)


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch):
    """Each test starts with no test client and demo_mode off."""
    quota_mod._set_test_client(None)
    monkeypatch.setattr(quota_mod.settings, "demo_mode", False)
    yield
    quota_mod._set_test_client(None)


@pytest.fixture
def fake_redis():
    """An in-process fake Redis exposing the async API."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    quota_mod._set_test_client(client)
    yield client


# ── Tier limits ─────────────────────────────────────────────────────────────

def test_tier_limit_defaults_unknown_to_free() -> None:
    assert _tier_limit(None) == quota_mod.settings.free_monthly_writes
    assert _tier_limit("garbage") == quota_mod.settings.free_monthly_writes


def test_tier_limit_starter_pro_enterprise() -> None:
    assert _tier_limit("starter") == quota_mod.settings.starter_monthly_writes
    assert _tier_limit("pro") == quota_mod.settings.pro_monthly_writes
    assert _tier_limit("enterprise") == quota_mod._UNLIMITED


def test_tier_limit_is_case_insensitive() -> None:
    assert _tier_limit("PRO") == quota_mod.settings.pro_monthly_writes


# ── Key + TTL helpers ───────────────────────────────────────────────────────

def test_quota_key_format() -> None:
    now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    assert _quota_key("u-123", now) == "quota:u-123:2026-05"


def test_seconds_until_month_end_is_positive_and_bounded() -> None:
    # On the first second of a month it should be roughly the seconds in the month.
    now = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)  # 28-day month
    s = _seconds_until_month_end_utc(now)
    assert 0 < s <= 28 * 24 * 3600


# ── No Redis configured → fail-open ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_redis_configured_is_a_no_op(monkeypatch) -> None:
    monkeypatch.setattr(quota_mod.settings, "redis_url", None)
    quota_mod._set_test_client(None)

    user = _user(tier="free")
    # Should pass through with no exception even after many calls.
    for _ in range(5):
        result = await enforce_write_quota(user=user)
        assert result is user


# ── Demo mode → no-op ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_demo_mode_bypasses_quota(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(quota_mod.settings, "demo_mode", True)
    monkeypatch.setattr(quota_mod.settings, "free_monthly_writes", 1)

    user = _user(tier="free")
    # Even though limit is 1 and we call it 3 times, demo mode skips Redis entirely.
    for _ in range(3):
        result = await enforce_write_quota(user=user)
        assert result is user
    # No counter should have been written.
    assert await fake_redis.get(_quota_key(str(user.id))) is None


# ── Under quota / at quota / over quota ─────────────────────────────────────

@pytest.mark.asyncio
async def test_under_quota_increments_counter(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(quota_mod.settings, "free_monthly_writes", 5)

    user = _user(tier="free")
    for expected in range(1, 4):
        await enforce_write_quota(user=user)
        assert int(await fake_redis.get(_quota_key(str(user.id)))) == expected


@pytest.mark.asyncio
async def test_at_quota_succeeds_then_429(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(quota_mod.settings, "free_monthly_writes", 3)

    user = _user(tier="free")
    # Three calls are allowed.
    for _ in range(3):
        await enforce_write_quota(user=user)

    # The fourth must be rejected with a structured 429 payload.
    with pytest.raises(QuotaExceeded) as exc:
        await enforce_write_quota(user=user)

    assert exc.value.status_code == 429
    assert exc.value.detail["error"] == "monthly_quota_exceeded"
    assert exc.value.detail["tier"] == "free"
    assert exc.value.detail["limit"] == 3
    assert exc.value.detail["used"] == 4  # post-increment count


@pytest.mark.asyncio
async def test_enterprise_tier_bypasses_quota(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(quota_mod.settings, "free_monthly_writes", 1)

    user = _user(tier="enterprise")
    for _ in range(50):
        await enforce_write_quota(user=user)
    # Enterprise should not even touch the counter.
    assert await fake_redis.get(_quota_key(str(user.id))) is None


@pytest.mark.asyncio
async def test_starter_and_pro_are_distinct_limits(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(quota_mod.settings, "starter_monthly_writes", 2)
    monkeypatch.setattr(quota_mod.settings, "pro_monthly_writes", 4)

    starter = _user(tier="starter")
    for _ in range(2):
        await enforce_write_quota(user=starter)
    with pytest.raises(QuotaExceeded):
        await enforce_write_quota(user=starter)

    pro = _user(tier="pro")
    for _ in range(4):
        await enforce_write_quota(user=pro)
    with pytest.raises(QuotaExceeded):
        await enforce_write_quota(user=pro)


# ── Fail-closed when Redis is configured but unreachable ────────────────────

class _BrokenRedis:
    async def incr(self, *_a, **_k):
        raise ConnectionError("redis is down")

    async def expire(self, *_a, **_k):
        return True


@pytest.mark.asyncio
async def test_redis_unreachable_when_configured_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(quota_mod.settings, "redis_url", "redis://broken-host:6379/0")
    quota_mod._set_test_client(_BrokenRedis())

    with pytest.raises(HTTPException) as exc:
        await enforce_write_quota(user=_user())
    assert exc.value.status_code == 503
    assert "Quota service unavailable" in str(exc.value.detail)


# ── TTL is set on the first increment of a month ────────────────────────────

@pytest.mark.asyncio
async def test_first_increment_sets_ttl(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr(quota_mod.settings, "free_monthly_writes", 100)

    user = _user(tier="free")
    await enforce_write_quota(user=user)

    ttl = await fake_redis.ttl(_quota_key(str(user.id)))
    # Either positive seconds or -1 (no expire). We expect a positive TTL.
    assert ttl is not None and ttl > 0
