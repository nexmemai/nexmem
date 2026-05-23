"""Redis-backed rate limiting using slowapi.

Phase 7 hardening:
* P7-E8 — per-authenticated-user key_func. The default key was
  ``X-Forwarded-For``-aware client IP, which means a CDN-rotated
  attacker could keep hammering a single user from many IPs while
  staying under the per-IP cap. The new ``user_id_or_ip`` key looks
  at the request's auth header first:

    1. ``Authorization: Bearer <jwt>`` — decode without raising, key
       on the JWT ``sub`` claim. Same logical user → same bucket.
    2. ``Authorization: ApiKey <raw>`` — hash the raw key and use a
       short prefix as the bucket. This is per-key, not per-user (the
       per-user mapping needs a DB lookup which is unsafe in a sync
       slowapi key_func), but it is still a strict tightening over
       per-IP and the bucket is stable across the key's life.
    3. No / invalid auth — fall back to the client IP.

  Per-route ``@limiter.limit(...)`` decorators automatically pick up
  this key_func; no decorator-site change is needed.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.core.security import decode_token


logger = logging.getLogger(__name__)


def get_client_ip(request) -> str:
    """Extract client IP, preferring X-Forwarded-For if behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


def _bearer_subject(credentials: str) -> Optional[str]:
    """Best-effort JWT decode. Returns ``sub`` or None.

    Never raises: a bad / expired / blocklisted token must NOT 500
    inside the rate-limit middleware. The caller falls back to IP
    for that request, which is the correct safe-default behaviour.
    """
    try:
        payload = decode_token(credentials)
    except Exception:
        return None
    sub = payload.get("sub") if isinstance(payload, dict) else None
    if not sub:
        return None
    if payload.get("type", "access") != "access":
        # Refresh / verification tokens go through dedicated routes
        # and should not collapse onto the access-token user bucket.
        return None
    return str(sub)


def user_id_or_ip(request) -> str:
    """slowapi key_func used by all per-route rate limits (P7-E8).

    Order: Bearer JWT user_id → API-key hash → client IP.
    Never raises; never returns an empty string (slowapi treats "" as
    "no key" which silently disables the limit).
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        sub = _bearer_subject(auth[len("Bearer "):])
        if sub:
            return f"user:{sub}"
    elif auth.startswith("ApiKey "):
        # Hash the raw key so the bucket survives header capitalisation
        # quirks and never logs the raw secret. Truncate so a very
        # long key does not bloat Redis keys.
        raw = auth[len("ApiKey "):].strip()
        if raw:
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            return f"apikey:{digest}"
    return f"ip:{get_client_ip(request) or 'unknown'}"


# Use Redis if available, fallback to memory.
storage_uri = getattr(settings, "redis_url", None) or "memory://"

limiter = Limiter(
    key_func=user_id_or_ip,  # P7-E8 — per-authenticated-user keying
    storage_uri=storage_uri,
    default_limits=["60/minute"],
    headers_enabled=True,
)
