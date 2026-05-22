"""Access-token blocklist (P3-A5, R-102).

Refresh tokens are revocable via the ``refresh_tokens`` table; access
tokens are not, so a stolen access token remains valid for up to
``ACCESS_TOKEN_EXPIRE_HOURS`` after the user changes their password
or notices the compromise. This module gives operators a way to
immediately kill an in-flight access token by its ``jti`` claim.

Storage: a Redis key ``access_blocklist:<jti>`` with TTL set to the
token's remaining lifetime. After expiry the key disappears and the
token would have been rejected anyway, so the blocklist is bounded
in size at ``access_token_expire_hours`` × peak revocation rate.

Behaviour when Redis is unavailable:

* ``revoke`` fails closed — it returns False and logs a warning so the
  caller (``/auth/change-password``, ``/auth/password-reset/confirm``)
  can surface a clear error rather than pretend the token is dead.
* ``is_revoked`` fails OPEN (returns False) — a Redis outage must not
  lock every authenticated user out. Defence in depth: refresh-token
  revocation in the database still applies, brute-force lockout still
  applies, and the access token's natural expiry is bounded at 4 h.

The blocklist is consulted from ``security.decode_token``, which is
the single chokepoint for every JWT-bearing request.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from app.config import settings


logger = logging.getLogger(__name__)


def _redis_client():
    """Return a synchronous Redis client, or ``None`` if unavailable.

    Mirrors ``app.core.celery_locks._redis_client`` but is kept
    separate so a future change in one module does not silently
    affect the other.
    """
    if not settings.redis_url:
        return None
    try:
        import redis  # noqa: WPS433

        return redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("token_blocklist: redis client init failed: %s", exc)
        return None


def _blocklist_key(jti: str) -> str:
    return f"access_blocklist:{jti}"


def revoke(jti: str, exp: Optional[int] = None) -> bool:
    """Mark an access token's ``jti`` as revoked.

    ``exp`` is the JWT ``exp`` claim (Unix timestamp). The Redis key
    is set with TTL = max(1, exp - now) so the entry expires when
    the token would have anyway. If ``exp`` is None we use the
    configured access-token lifetime as a safe upper bound.

    Returns True if the entry was persisted, False if Redis was
    unavailable. Callers should treat False as "revocation not
    durable" and surface an operator-visible error.
    """
    if not jti:
        return False
    client = _redis_client()
    if client is None:
        logger.warning(
            "token_blocklist.revoke: redis unavailable; jti=%s NOT durably revoked",
            jti,
        )
        return False

    if exp is not None:
        ttl = int(exp) - int(datetime.utcnow().timestamp())
    else:
        ttl = settings.access_token_expire_hours * 3600
    ttl = max(1, ttl)

    try:
        client.setex(_blocklist_key(jti), ttl, "1")
        logger.info(
            "token_blocklist: revoked jti=%s (ttl=%ss)", jti, ttl
        )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("token_blocklist.revoke: redis setex failed: %s", exc)
        return False


def is_revoked(jti: Optional[str]) -> bool:
    """Return True if ``jti`` is on the blocklist.

    Fail-open semantics: a Redis outage returns False so the entire
    authenticated surface does not lock out under a Redis incident.
    The access-token lifetime cap (4 h default) is the worst-case
    bound. ``revoke`` fails closed so abuse cannot exploit the
    fail-open here without an attacker also taking down Redis.
    """
    if not jti:
        return False
    client = _redis_client()
    if client is None:
        return False
    try:
        return client.exists(_blocklist_key(jti)) > 0
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("token_blocklist.is_revoked: redis unavailable: %s", exc)
        return False
