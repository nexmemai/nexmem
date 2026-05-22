"""Per-user monthly write quota enforcement.

This module provides a FastAPI dependency that increments and checks a
Redis counter keyed by `quota:<user_id>:<YYYY-MM>`. Returns 429 with a
structured payload when the user's tier limit is exceeded.

Design:
- Async-first. We use `redis.asyncio` so quota checks integrate with the
  existing FastAPI request loop.
- Fail-open on missing config in dev/test. If `REDIS_URL` is unset, the
  dependency is a no-op so local development is not blocked.
- Fail-closed on Redis errors when configured. If `REDIS_URL` is set and
  Redis is unreachable in production, we refuse the write (503). This is
  the correct policy: silently allowing unbounded writes when the throttle
  is broken is precisely the failure mode we are guarding against.
- The Redis key carries a TTL of "now → end-of-month UTC". On the first
  increment of a month we set the expiry; subsequent INCRs preserve it.
- Demo mode is a no-op so the in-memory test environment doesn't need
  Redis.

The single source of tier limits is `app.config.settings`.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status

from app.config import settings
from app.core.deps import get_current_user
from app.models.user import User


logger = logging.getLogger(__name__)


# ── Tier helpers ────────────────────────────────────────────────────────────

_UNLIMITED = -1  # sentinel for "no limit" (enterprise)


def _tier_limit(tier: Optional[str]) -> int:
    """Map a user's tier to their monthly write limit. Unknown tier → free."""
    t = (tier or "free").lower()
    if t == "enterprise":
        return _UNLIMITED
    if t == "pro":
        return settings.pro_monthly_writes
    if t == "starter":
        return settings.starter_monthly_writes
    return settings.free_monthly_writes


def _seconds_until_month_end_utc(now: Optional[datetime] = None) -> int:
    """Seconds from `now` until the last second of the current UTC month.

    Used as the Redis TTL on the first increment of a month so the key
    auto-expires at the calendar boundary.
    """
    now = now or datetime.now(timezone.utc)
    _, days = calendar.monthrange(now.year, now.month)
    end = datetime(now.year, now.month, days, 23, 59, 59, tzinfo=timezone.utc)
    return max(int((end - now).total_seconds()), 1)


def _quota_key(user_id: str, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"quota:{user_id}:{now.strftime('%Y-%m')}"


# ── Redis client (lazy, async, single instance) ─────────────────────────────

_redis_client = None  # type: ignore[assignment]


async def _get_redis():
    """Return the shared async Redis client, or None if no REDIS_URL is set."""
    global _redis_client
    if not settings.redis_url:
        return None
    if _redis_client is None:
        # Imported lazily so test environments that patch this module do not
        # require a real `redis.asyncio` install at import time.
        import redis.asyncio as redis_async

        _redis_client = redis_async.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True
        )
    return _redis_client


# Hook for tests to inject a fake Redis without monkey-patching
# settings.redis_url. When set, this overrides the lazy client.
_test_client_override = None  # type: ignore[assignment]


def _set_test_client(client) -> None:
    """Test-only: override the Redis client used by check_quota."""
    global _test_client_override
    _test_client_override = client


# ── Public dependency ───────────────────────────────────────────────────────


class QuotaExceeded(HTTPException):
    """429 raised when a user has exceeded their monthly write quota."""

    def __init__(self, *, tier: str, limit: int, used: int):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "monthly_quota_exceeded",
                "tier": tier,
                "limit": limit,
                "used": used,
                "resets": "first day of next month UTC",
            },
        )


async def enforce_write_quota(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency that increments and checks the user's write quota.

    Returns the user (so routers that already depend on `get_current_user`
    can replace that dependency with this one without changing signatures).

    Behaviour:
    - Demo mode: no-op.
    - REDIS_URL unset: fail-open (no-op). The deployment opted out of
      quotas (e.g. local dev).
    - Redis unreachable while configured: fail-closed with 503 — never
      silently let writes through when the throttle is broken.
    - Quota exceeded: raise 429 with structured payload.
    """
    if settings.demo_mode:
        return user

    client = _test_client_override or await _get_redis()
    if client is None:
        # No Redis configured. Quotas explicitly disabled by deployment.
        return user

    tier = getattr(user, "tier", None) or "free"
    limit = _tier_limit(tier)
    if limit == _UNLIMITED:
        return user

    key = _quota_key(str(user.id))

    try:
        current = await client.incr(key)
        if current == 1:
            ttl = _seconds_until_month_end_utc()
            await client.expire(key, ttl)
    except Exception as exc:  # noqa: BLE001
        # Configured but unreachable. Fail-closed.
        logger.error("Quota check failed; refusing write: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Quota service unavailable; write rejected to protect tenant limits.",
        ) from exc

    # `current` is the post-increment count. The user has *used* this many
    # writes this month including the current one. Reject if it exceeds the
    # limit, but only after consuming the increment so subsequent retries
    # still reflect actual attempts.
    if current > limit:
        raise QuotaExceeded(tier=tier, limit=limit, used=int(current))

    return user
