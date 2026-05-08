"""Per-user monthly token quota enforcement."""

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import TokenUsage, User


def _month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return month_start, next_month


def quota_for_tier(tier: str | None) -> int:
    tier_name = (tier or "free").lower()
    quotas = {
        "free": settings.free_monthly_writes,
        "starter": settings.starter_monthly_writes,
        "pro": settings.pro_monthly_writes,
        "enterprise": settings.enterprise_monthly_writes,
    }
    return int(quotas.get(tier_name, settings.free_monthly_writes))


async def get_monthly_usage(db: AsyncSession, user_id: Any) -> int:
    month_start, _ = _month_bounds()
    result = await db.execute(
        select(func.coalesce(func.sum(TokenUsage.total_tokens), 0)).where(
            TokenUsage.user_id == user_id,
            TokenUsage.created_at >= month_start,
        )
    )
    return int(result.scalar() or 0)


async def get_usage_quota_status(db: AsyncSession, user: User) -> Dict[str, Any]:
    _, reset_at = _month_bounds()
    used = await get_monthly_usage(db, user.id)
    quota = quota_for_tier(getattr(user, "tier", None))
    remaining = max(quota - used, 0)
    return {
        "tier": getattr(user, "tier", None) or "free",
        "quota": quota,
        "used": used,
        "remaining": remaining,
        "reset_at": reset_at.isoformat(),
    }


def get_empty_usage_quota_status(user: User) -> Dict[str, Any]:
    _, reset_at = _month_bounds()
    quota = quota_for_tier(getattr(user, "tier", None))
    return {
        "tier": getattr(user, "tier", None) or "free",
        "quota": quota,
        "used": 0,
        "remaining": quota,
        "reset_at": reset_at.isoformat(),
    }


def quota_headers(status_data: Dict[str, Any]) -> Dict[str, str]:
    headers = {
        "X-RateLimit-Limit": str(status_data["quota"]),
        "X-RateLimit-Remaining": str(status_data["remaining"]),
        "X-RateLimit-Reset": str(status_data["reset_at"]),
    }
    if "estimated_tokens" in status_data:
        headers["X-RateLimit-Estimated-Cost"] = str(status_data["estimated_tokens"])
    if "remaining_after_estimate" in status_data:
        headers["X-RateLimit-Remaining-After-Estimate"] = str(
            status_data["remaining_after_estimate"]
        )
    return headers


async def acquire_usage_quota_lock(db: AsyncSession, user_id: Any) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"usage_quota:{user_id}"},
    )


def estimate_rag_request_tokens(
    *,
    user_message: str,
    episodic_context: list[str] | None = None,
    semantic_context: list[str] | None = None,
    procedural_context: dict[str, Any] | None = None,
    graph_context: list[str] | None = None,
    model: str | None = None,
) -> int:
    from app.services.llm import (
        MAX_COMPLETION_TOKENS,
        SYSTEM_INSTRUCTION_RESERVE_TOKENS,
        count_tokens,
    )

    model_name = model or settings.openai_llm_model
    parts: list[str] = [user_message or ""]
    parts.extend(episodic_context or [])
    parts.extend(semantic_context or [])
    parts.extend(graph_context or [])
    if procedural_context:
        parts.append(str(procedural_context))

    prompt_tokens = sum(count_tokens(str(part), model_name) for part in parts if part)
    return prompt_tokens + SYSTEM_INSTRUCTION_RESERVE_TOKENS + MAX_COMPLETION_TOKENS


async def enforce_usage_quota(
    db: AsyncSession,
    user: User,
    *,
    estimated_tokens: int | None = None,
    acquire_lock: bool = False,
) -> Dict[str, Any]:
    if acquire_lock:
        await acquire_usage_quota_lock(db, user.id)

    status_data = await get_usage_quota_status(db, user)
    if status_data["remaining"] <= 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Monthly usage quota exceeded",
                **status_data,
            },
            headers=quota_headers(status_data),
        )

    if estimated_tokens is not None:
        estimate = max(int(estimated_tokens), 0)
        status_data["estimated_tokens"] = estimate
        status_data["remaining_after_estimate"] = max(
            status_data["remaining"] - estimate,
            0,
        )
        if estimate > status_data["remaining"]:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Estimated request would exceed monthly usage quota",
                    **status_data,
                },
                headers=quota_headers(status_data),
            )
    return status_data
