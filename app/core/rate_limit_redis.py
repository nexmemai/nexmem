"""Redis-based rate limiting with quotas using slowapi."""

import time
from typing import Callable, Optional
from fastapi import Request, HTTPException, status
from slowapi import Limiter
from slowapi.storage import RedisStorage
from slowapi.util import get_remote_address
from app.config import settings
from app.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db


def get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


# Initialize Redis storage (if Redis URL is provided)
if hasattr(settings, 'redis_url') and settings.redis_url:
    storage = RedisStorage.from_url(settings.redis_url)
    limiter = Limiter(
        key_func=get_client_ip,
        storage=storage,
        default_limits=["1000 per day", "60 per minute"]
    )
else:
    # Fallback to in-memory (for development)
    limiter = Limiter(
        key_func=get_client_ip,
        default_limits=["1000 per day", "60 per minute"]
    )


async def check_quota(
    request: Request,
    user: User = None,
    db: AsyncSession = None,
) -> None:
    """
    Check if user has exceeded their monthly write quota.
    Uses Redis counters with automatic monthly expiration.
    """
    if not user:
        return
    
    # Get user tier (default to 'free' if not set)
    tier = getattr(user, 'tier', 'free') or 'free'
    
    # Define quotas per tier
    quotas = {
        'free': 1000,
        'starter': 10000,
        'pro': 100000,
        'enterprise': float('inf'),
    }
    
    monthly_quota = quotas.get(tier, 1000)
    if monthly_quota == float('inf'):
        return  # Enterprise has no quota
    
    # Redis key for monthly writes: "quota:{user_id}:{year_month}"
    year_month = time.strftime("%Y-%m")
    redis_key = f"quota:{user.id}:{year_month}"
    
    try:
        import redis
        redis_client = redis.from_url(settings.redis_url)
        
        # Increment and get current count
        current = redis_client.incr(redis_key)
        
        # Set expiration to end of month if first write
        if current == 1:
            # Calculate seconds until end of month
            import calendar
            from datetime import datetime
            now = datetime.now()
            _, days_in_month = calendar.monthrange(now.year, now.month)
            seconds_until_month_end = int((
                datetime(now.year, now.month, days_in_month, 23, 59, 59)
                - now
            ).total_seconds())
            redis_client.expire(redis_key, max(seconds_until_month_end, 1))
        
        if current > monthly_quota:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Monthly quota exceeded",
                    "tier": tier,
                    "quota": monthly_quota,
                    "used": current - 1,
                    "resets": "First day of next month",
                }
            )
    except ImportError:
        # Redis not available - degrade gracefully
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Redis not available - skipping quota check")
    except Exception as e:
        # Log but don't block the request
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Quota check failed: {e}")


def rate_limit_middleware() -> Callable:
    """Create a dependency for rate limiting."""
    async def dependency(
        request: Request,
        user: User = None,
        db: AsyncSession = None,
    ) -> None:
        # Check rate limit (slowapi handles this)
        # Check quota (custom logic)
        if user and request.method in ["POST", "PUT", "PATCH"]:
            await check_quota(request, user, db)
    
    return dependency
