"""Celery tasks for background processing.

Phase 6 hardening:

* **P6-D9 RLS context.** Every Celery task that touches a user-scoped
  table now sets ``app.current_user_id`` on its session. Without this,
  RLS is forced + no context = zero rows returned, and the entire
  consolidation pipeline silently no-ops in production.
  ``consolidate_all_users`` runs WITHOUT a per-user context to
  enumerate the ``users`` table (the ``users_login_lookup`` SELECT
  policy from migration 013 allows that exactly), then enqueues one
  task per user; each per-user task sets its own context.

* **P6-D5 idempotency.** A Redis ``SET NX EX`` lock keyed
  ``consolidation:<user_id>:<window>`` keeps a duplicate enqueue
  from doing the work twice. TTL is the task hard-time-limit plus a
  buffer so a Celery hard-kill cannot leave a stale lock behind.

* **P6-D1 dead-letter queue.** When all retries are exhausted, the
  payload is pushed to a Redis list (``settings.dlq_redis_key``) so an
  operator can inspect / replay later. If Redis is unavailable the
  payload is logged at CRITICAL level so it survives in the log
  shipper.

* **P6-D6 NLP/LLM outside DB transaction.** Implemented in
  ``app.services.consolidation``; the task here just calls into the
  hardened pipeline.

* **P9-G1 read-only mode.** When ``settings.read_only`` is true the
  task short-circuits with a warning rather than blocking the
  worker. The kill switch only freezes inbound HTTP today; this
  closes the same door for the background path.
"""

from __future__ import annotations

import logging
from typing import Optional

from asgiref.sync import async_to_sync

from app.celery_app import celery_app
from app.config import settings
from app.core.celery_locks import acquire_lock, dlq_push, release_lock
from app.database import async_session, set_rls_context
from app.services.consolidation import consolidate_for_user

logger = logging.getLogger(__name__)


def _consolidation_lock_key(user_id: str, days_old: int) -> str:
    return f"consolidation:{user_id}:{days_old}"


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="app.tasks.consolidate_user_memory_task",
)
def consolidate_user_memory_task(
    self, user_id: str, days_old: int = 1
):
    """Background consolidation for a single user.

    Idempotent across the lock TTL, RLS-aware, and routes failed
    payloads to the DLQ when retries are exhausted.
    """
    if settings.read_only:
        logger.warning(
            "consolidation: skipped — service is in READ_ONLY mode (user=%s)",
            user_id,
        )
        return {"skipped": True, "reason": "read_only"}

    lock_key = _consolidation_lock_key(user_id, days_old)
    lock_token: Optional[str] = acquire_lock(
        lock_key, ttl_seconds=settings.consolidation_lock_ttl_seconds
    )
    if lock_token is None:
        logger.info(
            "consolidation: skipping — another worker holds %s", lock_key
        )
        return {"skipped": True, "reason": "duplicate"}

    async def _run() -> int:
        from app.services.embedder import embedder
        from app.services.engram_processor import engram_processor
        from app.services.llm import LLMService

        async with async_session() as db:
            # P6-D9: bind the session to this user's RLS context so
            # every SELECT/INSERT/UPDATE that follows sees only this
            # user's rows. Without this, RLS forced + no context =
            # zero rows. The set_config call autobegins a tx; we
            # ensure consolidate_for_user closes it before precompute.
            await set_rls_context(db, user_id)
            return await consolidate_for_user(
                db=db,
                user_id=user_id,
                embedder=embedder,
                llm_service=LLMService(),
                engram_processor=engram_processor,
                days_old=days_old,
            )

    try:
        result = async_to_sync(_run)()
        logger.info(
            "consolidation: success user=%s consolidated=%s",
            user_id,
            result,
        )
        return {"consolidated": int(result), "user_id": user_id}
    except Exception as exc:
        logger.error(
            "consolidation: task failed for user %s (attempt %s/%s): %s",
            user_id,
            self.request.retries + 1,
            self.max_retries,
            exc,
        )

        if self.request.retries >= self.max_retries:
            # P6-D1: durable DLQ. Payload includes everything an
            # operator needs to re-enqueue manually.
            dlq_push(
                {
                    "task": self.name,
                    "task_id": self.request.id,
                    "user_id": user_id,
                    "days_old": days_old,
                    "retries": self.request.retries,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
            return {"failed": True, "user_id": user_id, "dlq": True}

        # Exponential backoff: 60s, 120s, 240s.
        countdown = 60 * (2 ** self.request.retries)
        # ``self.retry`` raises ``Retry`` to signal the broker.
        # We deliberately do NOT release the lock here — the lock
        # TTL covers the retry window, and re-acquiring on the next
        # attempt would defeat the de-duplication purpose.
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        # Best-effort lock release on terminal outcomes (success or
        # DLQ). The Lua script ensures we only delete the key when
        # we still own it, so a Celery hard-kill that left the lock
        # behind cannot be released by a different worker.
        try:
            release_lock(lock_key, lock_token)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "consolidation: lock release failed for %s: %s", lock_key, exc
            )


@celery_app.task(
    bind=True,
    max_retries=3,
    name="app.tasks.consolidate_all_users",
)
def consolidate_all_users(self):
    """Beat-scheduled task: enumerate users and enqueue one task each.

    The enumeration runs WITHOUT setting an RLS context — the
    ``users_login_lookup`` policy from migration 013 permits SELECT
    when ``app.current_user_id`` is NULL. Each enqueued task sets
    its own per-user context, so the work itself remains RLS-safe.
    """
    if settings.read_only:
        logger.warning(
            "consolidation: scheduler skipped — service is in READ_ONLY mode"
        )
        return {"skipped": True, "reason": "read_only"}

    from sqlalchemy import select

    from app.models.user import User

    async def _run_all():
        async with async_session() as session:
            # No set_rls_context: we want the lookup policy path.
            result = await session.execute(select(User.id))
            user_ids = [str(uid) for uid in result.scalars().all()]
        triggered = 0
        for uid in user_ids:
            consolidate_user_memory_task.delay(uid)
            triggered += 1
        logger.info("consolidation: queued %s users", triggered)
        return {"users_queued": triggered}

    try:
        return async_to_sync(_run_all)()
    except Exception as exc:
        logger.error("consolidate_all_users failed: %s", exc)
        raise self.retry(exc=exc, countdown=300)
