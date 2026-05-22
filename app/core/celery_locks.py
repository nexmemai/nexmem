"""Redis-backed locks and dead-letter queue for Celery tasks (P6-D1, P6-D5).

Two small primitives, both null-safe so the test suite can exercise
the call-sites without a live Redis:

* ``acquire_lock`` / ``release_lock`` (P6-D5).
  ``SET <key> <token> NX EX <ttl>`` is the canonical Redis idempotency
  pattern. The token is a random per-acquisition value; release uses
  a Lua script so we only delete the key if we still own it (avoids
  the classic "expire-then-someone-else-acquired-then-we-released"
  race).

* ``dlq_push`` (P6-D1).
  Failed-permanently task payloads are pushed to a Redis list
  ``settings.dlq_redis_key`` (default ``nexmem:dlq:consolidation``).
  An operator can inspect with ``LRANGE`` and a future
  ``nexmem-admin replay-dlq`` CLI can ``LPOP`` and re-enqueue.
  The payload includes the original task id, the user id, the
  window, the captured exception string, and a UTC timestamp; the
  full traceback is in the workers' structured logs.

Behaviour when Redis is unavailable:

* Lock helpers fail-open (``acquire_lock`` returns ``True``) so that
  a Redis outage does not block consolidation entirely. The price is
  that an idempotency window is best-effort. Production logs a
  warning in the structured pipeline.
* DLQ helper fails-closed in the sense that it logs a CRITICAL line
  with the entire payload, so the data is never lost — it shows up
  in Sentry / log shipper instead of Redis. Operators replay from
  log archives in the worst case.

The module imports ``redis`` lazily; demo-mode tests do not need
Redis to be importable.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, Optional

from app.config import settings


logger = logging.getLogger(__name__)


# ── Connection ───────────────────────────────────────────────────────────────
def _redis_client():
    """Return a synchronous Redis client, or ``None`` if unavailable.

    Celery workers run synchronously inside ``async_to_sync``; using
    sync redis-py is the simplest path that does not need its own
    event loop. The client is cheap to construct so we don't pool —
    the cost is dwarfed by the consolidation work it guards.
    """
    if not settings.redis_url:
        return None
    try:
        import redis  # noqa: WPS433  -- import inside function is intentional

        return redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("celery_locks: redis client init failed: %s", exc)
        return None


# ── P6-D5: idempotency lock ──────────────────────────────────────────────────
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def acquire_lock(key: str, ttl_seconds: int) -> Optional[str]:
    """Attempt to acquire ``key`` with ``SET NX EX``.

    Returns the random release token on success. Returns ``None`` if
    the key is already held by another worker — the caller should
    short-circuit and skip the work.

    On Redis failure we fail open: returns a synthetic token so the
    caller can proceed. The structured logger records the
    unavailability so an operator can correlate.
    """
    client = _redis_client()
    if client is None:
        return "no-redis"
    token = secrets.token_hex(16)
    try:
        ok = client.set(name=key, value=token, nx=True, ex=int(ttl_seconds))
        if ok:
            return token
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "celery_locks.acquire_lock: redis unavailable (%s); failing open", exc
        )
        return "no-redis"


def release_lock(key: str, token: str) -> bool:
    """Release ``key`` only if we still own it.

    The Lua script is atomic in Redis; a release after our token
    expired and another worker acquired the key is a no-op. Returns
    True if we deleted the key, False otherwise.
    """
    if token == "no-redis":
        return False
    client = _redis_client()
    if client is None:
        return False
    try:
        result = client.eval(_RELEASE_LUA, 1, key, token)
        return bool(result)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("celery_locks.release_lock: redis unavailable (%s)", exc)
        return False


# ── P6-D1: dead-letter queue ─────────────────────────────────────────────────
def dlq_push(payload: Dict[str, Any]) -> bool:
    """Append a failed-task payload to the DLQ Redis list.

    Returns True if the entry was persisted to Redis, False if it
    fell back to a CRITICAL log line. Either way the data is
    recoverable.
    """
    enriched = {
        **payload,
        "dlq_at": datetime.utcnow().isoformat() + "Z",
    }
    client = _redis_client()
    if client is None:
        # Always log so the data survives even when Redis is down.
        logger.critical(
            "DLQ-FALLBACK: redis unavailable, payload follows: %s",
            json.dumps(enriched),
        )
        return False
    try:
        client.lpush(settings.dlq_redis_key, json.dumps(enriched))
        # Bound the queue to a sane size so a stuck-task storm
        # cannot exhaust Redis memory. Trim keeps the most recent.
        client.ltrim(settings.dlq_redis_key, 0, settings.dlq_max_entries - 1)
        logger.warning(
            "DLQ: pushed task to %s (queue depth ~ %s)",
            settings.dlq_redis_key,
            client.llen(settings.dlq_redis_key),
        )
        return True
    except Exception as exc:
        logger.critical(
            "DLQ-FALLBACK: redis lpush failed (%s), payload follows: %s",
            exc,
            json.dumps(enriched),
        )
        return False


def dlq_peek(limit: int = 50) -> list[Dict[str, Any]]:
    """Read the most recent ``limit`` DLQ entries (newest first).

    Used by tests and by future ``nexmem-admin replay-dlq`` tooling.
    Returns an empty list when Redis is unavailable.
    """
    client = _redis_client()
    if client is None:
        return []
    try:
        raw = client.lrange(settings.dlq_redis_key, 0, max(0, limit - 1))
        out: list[Dict[str, Any]] = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except Exception:
                out.append({"raw": item})
        return out
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("celery_locks.dlq_peek: redis unavailable (%s)", exc)
        return []
