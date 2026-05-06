"""Redis-backed rate limiting using slowapi."""

from slowapi import Limiter
from slowapi.util import get_remote_address
from app.config import settings

def get_client_ip(request):
    """Extract client IP, preferring X-Forwarded-For if behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)

# Use Redis if available, fallback to memory
storage_uri = getattr(settings, 'redis_url', None) or "memory://"

limiter = Limiter(
    key_func=get_client_ip,
    storage_uri=storage_uri,
    default_limits=["60/minute"],
    headers_enabled=True,
)