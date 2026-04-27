"""Simple rate limiting middleware."""

import time
from collections import defaultdict
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter (60 requests/minute per IP)."""

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests = defaultdict(list)

    def _clean_old_requests(self, ip: str) -> None:
        """Remove requests older than 1 minute."""
        cutoff = time.time() - 60
        self.requests[ip] = [t for t in self.requests[ip] if t > cutoff]

    def _is_rate_limited(self, ip: str) -> bool:
        """Check if IP is rate limited."""
        self._clean_old_requests(ip)
        return len(self.requests[ip]) >= self.requests_per_minute

    async def dispatch(self, request: Request, call_next):
        ip = request.client.host
        if self._is_rate_limited(ip):
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later."
            )
        self.requests[ip].append(time.time())
        return await call_next(request)