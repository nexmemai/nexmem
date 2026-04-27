"""Simple rate limiting middleware with memory-safe cleanup."""

import time
import threading
from collections import defaultdict
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter (60 requests/minute per IP)."""

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests = defaultdict(list)
        self._cleanup_interval = 300
        self._last_cleanup = time.time()
        self._lock = threading.Lock()

        self._start_cleanup_thread()

    def _start_cleanup_thread(self):
        """Start background thread to clean up old entries."""
        def cleanup_task():
            while True:
                time.sleep(self._cleanup_interval)
                self._cleanup_old_entries()

        thread = threading.Thread(target=cleanup_task, daemon=True)
        thread.start()

    def _cleanup_old_entries(self):
        """Remove requests older than 2 minutes."""
        cutoff = time.time() - 120
        with self._lock:
            ips_to_remove = []
            for ip, timestamps in self.requests.items():
                self.requests[ip] = [t for t in timestamps if t > cutoff]
                if not self.requests[ip]:
                    ips_to_remove.append(ip)
            for ip in ips_to_remove:
                del self.requests[ip]

    def _clean_old_requests(self, ip: str) -> None:
        """Remove requests older than 1 minute for specific IP."""
        cutoff = time.time() - 60
        with self._lock:
            self.requests[ip] = [t for t in self.requests[ip] if t > cutoff]

    def _is_rate_limited(self, ip: str) -> bool:
        """Check if IP is rate limited."""
        self._clean_old_requests(ip)
        with self._lock:
            return len(self.requests[ip]) >= self.requests_per_minute

    async def dispatch(self, request: Request, call_next):
        ip = request.client.host
        if self._is_rate_limited(ip):
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later."
            )
        with self._lock:
            self.requests[ip].append(time.time())
        return await call_next(request)