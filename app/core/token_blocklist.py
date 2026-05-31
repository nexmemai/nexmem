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



# ── P11-I3 (Block 6): user-level force-logout ────────────────────────────────
# Per-jti revocation (above) is fine when the operator holds the token
# they want to kill. An admin force-logout doesn't have the user's bearer
# header — they only have the user_id — so we need a separate primitive:
# a per-user CUTOFF TIMESTAMP. Every access token carries an ``iat`` claim
# (Block 6 addition); ``decode_token`` rejects any access token whose iat
# is strictly less than the stored cutoff. Tokens issued AFTER the cutoff
# (because the user re-logged in legitimately) pass through, so the
# operator does not have to manually clear the entry.
#
# Storage: Redis key ``user_blocklist:<user_id>`` = unix timestamp string.
# TTL defaults to the configured access-token lifetime — once every
# pre-cutoff access token has expired naturally, the cutoff entry is
# pointless and Redis can drop it.
#
# Failure posture matches the per-jti version:
#   ``revoke_user_tokens`` fails CLOSED (returns False on Redis outage so
#       the admin route can surface 503).
#   ``get_user_revocation_cutoff`` fails OPEN (returns None on Redis
#       outage so a Redis incident doesn't lock every authenticated user
#       out for 4h). Defence in depth: refresh-token revocation in the
#       database still applies when this fails open.


def revoke_user_tokens(user_id: str, ttl_seconds: Optional[int] = None) -> bool:
    """Force-logout: every access token for ``user_id`` issued before now()
    is rejected by ``decode_token``.

    Stores ``user_blocklist:<user_id>`` = current unix timestamp with TTL =
    ``ttl_seconds`` (default = configured access-token lifetime). Returns
    True if the entry was persisted, False on Redis unavailability.
    """
    if not user_id:
        return False
    client = _redis_client()
    if client is None:
        logger.warning(
            "token_blocklist.revoke_user_tokens: redis unavailable; "
            "user_id=%s NOT durably revoked",
            user_id,
        )
        return False

    ttl = ttl_seconds if ttl_seconds is not None else (
        settings.access_token_expire_hours * 3600
    )
    ttl = max(1, int(ttl))
    cutoff = int(datetime.utcnow().timestamp())
    try:
        client.setex(f"user_blocklist:{user_id}", ttl, str(cutoff))
        logger.info(
            "token_blocklist: force-logout user_id=%s cutoff=%s ttl=%ss",
            user_id,
            cutoff,
            ttl,
        )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "token_blocklist.revoke_user_tokens: redis setex failed: %s", exc
        )
        return False


def get_user_revocation_cutoff(user_id: str) -> Optional[int]:
    """Return the unix-timestamp cutoff for ``user_id``, or ``None``.

    A return of ``None`` means either (a) the user has not been
    force-logged-out, or (b) Redis is unavailable. Both cases pass
    through; see ``revoke_user_tokens`` docstring for the rationale.
    """
    if not user_id:
        return None
    client = _redis_client()
    if client is None:
        return None
    try:
        raw = client.get(f"user_blocklist:{user_id}")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "token_blocklist.get_user_revocation_cutoff: redis get failed: %s",
            exc,
        )
        return None
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        # Stale or malformed entry — log and treat as not revoked.
        logger.warning(
            "token_blocklist.get_user_revocation_cutoff: unparseable value %r",
            raw,
        )
        return None
