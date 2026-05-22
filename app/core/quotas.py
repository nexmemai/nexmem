"""Per-user monthly write/read quotas backed by Redis.

Phase 2 (R-005, R-108):
* Phase 1 defined ``check_quota`` in ``app/core/rate_limit_redis.py``
  but never called it. This module is the canonical implementation
  and is wired into every write route + the read-heavy /memory/context
  and /rag/chat routes.
* Quotas are reset by Redis key expiry on the first day of the next
  month. The first INC of the month sets EXPIREAT to that boundary.
* Demo mode has no Redis and therefore no enforcement; the dependency
  is a no-op so the test suite can exercise the call path without
  external infrastructure.
* If Redis is configured but unreachable, we **fail closed**: the
  caller gets HTTP 503. The original Phase 1 stub failed open, which
  defeated the protection.
"""
from __future__ import annotations

import calendar
import logging
import time
from datetime import datetime
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status

from app.config import settings
from app.core.deps import get_current_user
from app.models.user import User


logger = logging.getLogger(__name__)


_INFINITE = float("inf")


def _write_cap_for(tier: str) -> float:
    return {
        "free": settings.free_monthly_writes,
        "starter": settings.starter_monthly_writes,
        "pro": settings.pro_monthly_writes,
        "enterprise": _INFINITE,
    }.get(tier, settings.free_monthly_writes)


def _read_cap_for(tier: str) -> float:
    return {
        "free": settings.free_monthly_reads,
        "starter": settings.starter_monthly_reads,
        "pro": settings.pro_monthly_reads,
        "enterprise": _INFINITE,
    }.get(tier, settings.free_monthly_reads)


def _seconds_until_month_end() -> int:
    now = datetime.utcnow()
    _, days_in_month = calendar.monthrange(now.year, now.month)
    end = datetime(now.year, now.month, days_in_month, 23, 59, 59)
    return max(int((end - now).total_seconds()), 1)


def _redis_client() -> Optional[aioredis.Redis]:
    if not settings.redis_url:
        return None
    return aioredis.from_url(settings.redis_url, socket_timeout=2)


async def _check_and_increment(user: User, kind: str, cap: float) -> int:
    """Return the post-increment count. Raises 429 if cap exceeded.

    On Redis errors, raises 503 (fail-closed). Demo mode never reaches
    this function because the wiring short-circuits.
    """
    client = _redis_client()
    if client is None:
        # No Redis configured. In demo mode this is fine; in production
        # the operator should set REDIS_URL. We log a warning once per
        # request rather than fail-open silently.
        logger.warning("quota: REDIS_URL is not set; allowing %s for user %s", kind, user.id)
        return 0

    if cap == _INFINITE:
        try:
            await client.aclose()
        except Exception:
            pass
        return 0

    year_month = time.strftime("%Y-%m")
    key = f"quota:{kind}:{user.id}:{year_month}"
    try:
        try:
            current = int(await client.incr(key))
        except Exception as exc:  # noqa: BLE001
            logger.error("quota: redis incr failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Quota service unavailable",
            )

        if current == 1:
            try:
                await client.expire(key, _seconds_until_month_end())
            except Exception as exc:  # noqa: BLE001
                logger.warning("quota: redis expire failed: %s", exc)
                # Not fatal; the key will still be incremented and
                # eventually cleaned by Redis maxmemory eviction.

        if current > cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Monthly quota exceeded",
                    "kind": kind,
                    "tier": getattr(user, "tier", "free") or "free",
                    "quota": int(cap),
                    "used": current - 1,
                    "resets_at": "first day of next month (UTC)",
                },
            )
        return current
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


async def enforce_write_quota(
    request: Request,
    user: User = Depends(get_current_user),
) -> None:
    """FastAPI dependency for write routes."""
    if settings.demo_mode:
        return
    tier = getattr(user, "tier", "free") or "free"
    await _check_and_increment(user, "write", _write_cap_for(tier))


async def enforce_read_quota(
    request: Request,
    user: User = Depends(get_current_user),
) -> None:
    """FastAPI dependency for read routes that traverse memory.

    The default read cap is generous (10000/month free). It is wired
    into /memory/context and /rag/chat where each call may fan out
    into vector search + reranker + LLM and is therefore expensive.
    """
    if settings.demo_mode:
        return
    tier = getattr(user, "tier", "free") or "free"
    await _check_and_increment(user, "read", _read_cap_for(tier))
