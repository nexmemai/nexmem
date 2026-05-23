"""Celery queue-depth backpressure (P6-D8, Block 7).

Wires a FastAPI dependency that 503s a write request when the
default Celery queue (``LLEN celery`` on the broker Redis) exceeds
``settings.celery_queue_depth_limit``.

Posture
-------
* **Fail-open on Redis outage.** ``get_queue_depth`` returns 0 if
  the broker is unreachable (no client, connection refused,
  timeout, etc.). A Redis outage already triggers /health/ready
  failure; doubling that into a write-side 503 would just turn a
  visible "Redis is down" incident into a confusing "writes are
  503ing for unknown reason" incident.

  This is consistent with R-301 (BACKEND_RISKS.md) — Redis
  fail-open is the documented private-beta posture.

* **Demo mode is a no-op.** No broker exists; the test suite
  exercises the dependency by monkeypatching ``get_queue_depth``
  directly.

* **One round-trip per call.** No socket caching across requests.
  Backpressure is on the write hot path; we accept the per-call
  TCP overhead (typically <1ms for a local Redis) in exchange for
  not having to manage a long-lived async client lifecycle inside
  a process that may be killed mid-request by graceful shutdown.

* **503, not 429.** The cause is server-side overload, not a
  per-client rate-limit violation. A retry from the same client
  should succeed once pressure subsides; a different status
  would mislead well-behaved clients.

Wiring
------
Used as a regular FastAPI dependency on the write routes that
enqueue Celery work (``POST /memory/episode/write``). Read routes
intentionally bypass it — a deep queue is irrelevant to a route
that only reads.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, status

from app.config import settings


logger = logging.getLogger(__name__)


async def get_queue_depth(queue_name: str = "celery") -> int:
    """Return the LLEN of a Celery queue, or 0 on any failure.

    Demo mode short-circuits — the test suite monkeypatches this
    when it needs a non-zero depth. Non-demo mode imports
    ``redis.asyncio`` lazily so the import cost is paid only when
    the broker is configured.
    """
    if settings.demo_mode:
        return 0
    if not settings.redis_url:
        # No broker configured: nothing to push back against. Treat
        # as zero so the dependency is a no-op rather than a 503.
        return 0
    try:
        import redis.asyncio as aioredis  # local import — see module docstring

        client = aioredis.from_url(
            settings.redis_url,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        try:
            depth = await client.llen(queue_name)
        finally:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover - defensive
                pass
        return int(depth)
    except Exception as exc:
        # R-301 fail-open posture. Log + continue. The
        # /health/ready Redis probe already alerts on this; we do
        # not need to also break write traffic.
        logger.warning(
            "queue_pressure: queue depth unavailable: %s", exc
        )
        return 0


async def check_queue_pressure() -> None:
    """FastAPI dependency: 503 the request if the queue is too deep.

    Returns ``None`` on success. The ``settings.celery_queue_depth_limit
    == 0`` case disables backpressure entirely (sentinel for
    operator opt-out).
    """
    if settings.celery_queue_depth_limit <= 0:
        return  # backpressure disabled
    depth = await get_queue_depth()
    if depth > settings.celery_queue_depth_limit:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "service_overloaded",
                "message": (
                    "Memory consolidation queue is full. "
                    "Please retry in a few minutes."
                ),
                "queue_depth": depth,
                "limit": settings.celery_queue_depth_limit,
            },
        )
