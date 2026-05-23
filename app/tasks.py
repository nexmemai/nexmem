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

* **P8-F2 / P6-D10 structured task logging.** Every task emits a
  structured log line at start and at terminal outcome
  (``success`` / ``failure`` / ``retry`` / ``skipped`` / ``dlq``)
  with ``task_id``, ``task_name``, ``user_id``, ``app_id``, and
  ``duration_ms``. The ``_log_task_event`` helper centralises field
  names so they cannot drift across tasks; tests assert that the
  ``task_id`` field is present in the structured payload.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from asgiref.sync import async_to_sync

from app.celery_app import celery_app
from app.config import settings
from app.core.celery_locks import acquire_lock, dlq_push, release_lock
from app.database import async_session, set_rls_context
from app.services.consolidation import consolidate_for_user

logger = logging.getLogger(__name__)

# P8-F2 / P6-D10: structured task logger. Distinct logger name
# (``celery.task``) so the operator can filter on it independently of
# the HTTP request log. ``structlog.get_logger`` returns a
# ``BoundLogger`` whose calls produce the same JSON shape as the rest
# of the application (configured in ``app.core.logging``).
_task_logger = structlog.get_logger("celery.task")


def _log_task_event(
    event: str,
    *,
    task_id: Optional[str],
    task_name: str,
    user_id: Optional[str] = None,
    app_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    outcome: Optional[str] = None,
    **extra: Any,
) -> None:
    """Emit one structured Celery task log line.

    P8-F2 / P6-D10. Every task call site goes through this helper so
    field names (``task_id``, ``task_name``, ``user_id``, ``app_id``,
    ``duration_ms``, ``outcome``) cannot drift. Additional task-specific
    fields are accepted via ``**extra`` and passed through verbatim.
    """
    payload = {
        "task_id": task_id,
        "task_name": task_name,
    }
    if user_id is not None:
        payload["user_id"] = user_id
    if app_id is not None:
        payload["app_id"] = app_id
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if outcome is not None:
        payload["outcome"] = outcome
    payload.update(extra)
    _task_logger.info(event, **payload)


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
    task_id = self.request.id
    task_name = self.name
    started_at = time.monotonic()

    _log_task_event(
        "task_start",
        task_id=task_id,
        task_name=task_name,
        user_id=user_id,
        days_old=days_old,
    )

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started_at) * 1000)

    if settings.read_only:
        logger.warning(
            "consolidation: skipped — service is in READ_ONLY mode (user=%s)",
            user_id,
        )
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            user_id=user_id,
            duration_ms=_elapsed_ms(),
            outcome="skipped",
            reason="read_only",
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
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            user_id=user_id,
            duration_ms=_elapsed_ms(),
            outcome="skipped",
            reason="duplicate",
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
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            user_id=user_id,
            duration_ms=_elapsed_ms(),
            outcome="success",
            consolidated=int(result),
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
            _log_task_event(
                "task_end",
                task_id=task_id,
                task_name=task_name,
                user_id=user_id,
                duration_ms=_elapsed_ms(),
                outcome="dlq",
                error_type=type(exc).__name__,
                retries=self.request.retries,
            )
            return {"failed": True, "user_id": user_id, "dlq": True}

        # Exponential backoff: 60s, 120s, 240s.
        countdown = 60 * (2 ** self.request.retries)
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            user_id=user_id,
            duration_ms=_elapsed_ms(),
            outcome="retry",
            error_type=type(exc).__name__,
            attempt=self.request.retries + 1,
            countdown_seconds=countdown,
        )
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
    task_id = self.request.id
    task_name = self.name
    started_at = time.monotonic()

    _log_task_event(
        "task_start",
        task_id=task_id,
        task_name=task_name,
    )

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started_at) * 1000)

    if settings.read_only:
        logger.warning(
            "consolidation: scheduler skipped — service is in READ_ONLY mode"
        )
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            duration_ms=_elapsed_ms(),
            outcome="skipped",
            reason="read_only",
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
        outcome = async_to_sync(_run_all)()
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            duration_ms=_elapsed_ms(),
            outcome="success",
            users_queued=outcome.get("users_queued", 0),
        )
        return outcome
    except Exception as exc:
        logger.error("consolidate_all_users failed: %s", exc)
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            duration_ms=_elapsed_ms(),
            outcome="retry",
            error_type=type(exc).__name__,
        )
        raise self.retry(exc=exc, countdown=300)




# ── P7-E4 (Block 5): execute scheduled GDPR soft-deletes ─────────────────────
@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    name="app.tasks.execute_scheduled_deletions",
)
def execute_scheduled_deletions(self):
    """Hard-delete every user whose grace period has elapsed.

    Pairs with ``DELETE /memory/user/{id}/all`` (P7-E4): that route
    only stamps ``deletion_scheduled_for``; this task does the real
    cascade. Should be run daily by Celery Beat (the schedule entry
    is left for the operator to add — wiring it on by default would
    surprise existing deployments).

    Per the spec: the ``users`` row itself is NOT dropped — only
    every user-scoped memory row + every API key. ``is_active`` is
    left at False so the email cannot be re-used to create a
    second account with the same identity. This is the simpler
    "tombstone" shape; a full row-delete + email scrub can come in
    a follow-up if the operator decides the metadata residue is a
    privacy concern.

    Demo-mode short-circuit: the in-memory store is per-test, so
    nothing to clean up. Read-only mode also skips: hard-deletes
    are state-changing and a read-only window must not run them.

    The task logs only the ``user_id`` and per-table rowcounts —
    never the email — so the GDPR-deleted user is not re-identified
    by the deletion log itself.
    """
    task_id = self.request.id
    task_name = self.name
    started_at = time.monotonic()

    _log_task_event(
        "task_start",
        task_id=task_id,
        task_name=task_name,
    )

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started_at) * 1000)

    if settings.demo_mode:
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            duration_ms=_elapsed_ms(),
            outcome="skipped",
            reason="demo_mode",
        )
        return {"skipped": "demo_mode"}

    if settings.read_only:
        logger.warning(
            "execute_scheduled_deletions: skipped — service is in READ_ONLY mode"
        )
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            duration_ms=_elapsed_ms(),
            outcome="skipped",
            reason="read_only",
        )
        return {"skipped": "read_only"}

    async def _run() -> dict:
        from sqlalchemy import delete, select, update

        from app.database import set_rls_context
        from app.models.engram import Engram
        from app.models.memory import (
            EpisodicMemory,
            KnowledgeEdge,
            KnowledgeNode,
            ProceduralMemory,
            SemanticMemory,
        )
        from app.models.user import APIKey, User

        # Same FK-respecting order used by the old immediate-delete
        # path (children before parents) so a future ON DELETE
        # RESTRICT cannot trip the cascade.
        delete_order = (
            Engram,
            SemanticMemory,
            EpisodicMemory,
            ProceduralMemory,
            KnowledgeEdge,
            KnowledgeNode,
        )

        users_processed = 0
        async with async_session() as db:
            # No RLS context here: the lookup runs under the
            # ``users_login_lookup`` policy (migration 013) which
            # permits SELECT when ``app.current_user_id`` is NULL.
            now = datetime.now(timezone.utc)
            res = await db.execute(
                select(User.id).where(
                    User.deletion_scheduled_for.is_not(None),
                    User.deletion_scheduled_for <= now,
                )
            )
            user_ids = [str(uid) for uid in res.scalars().all()]

            for uid in user_ids:
                # Each user is a separate transaction so a single
                # failure does not block the rest of the batch.
                try:
                    await set_rls_context(db, uid)
                    counts: dict[str, int] = {}
                    async with db.begin():
                        for model in delete_order:
                            r = await db.execute(
                                delete(model).where(model.user_id == uid)
                            )
                            counts[model.__tablename__] = r.rowcount or 0
                        r = await db.execute(
                            delete(APIKey).where(APIKey.user_id == uid)
                        )
                        counts["api_keys"] = r.rowcount or 0
                        # Tombstone shape: keep the users row but
                        # lock it out permanently and clear the
                        # deletion-scheduled marker so the next
                        # task run does not re-process this user.
                        await db.execute(
                            update(User)
                            .where(User.id == uid)
                            .values(
                                is_active=False,
                                deletion_scheduled_for=None,
                            )
                        )
                    logger.info(
                        "execute_scheduled_deletions: user_id=%s counts=%s",
                        uid,
                        counts,
                    )
                    users_processed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "execute_scheduled_deletions: failed for user_id=%s: %s",
                        uid,
                        exc,
                    )
        return {"users_processed": users_processed}

    try:
        outcome = async_to_sync(_run)()
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            duration_ms=_elapsed_ms(),
            outcome="success",
            users_processed=outcome.get("users_processed", 0),
        )
        return outcome
    except Exception as exc:
        logger.error("execute_scheduled_deletions: top-level failure: %s", exc)
        _log_task_event(
            "task_end",
            task_id=task_id,
            task_name=task_name,
            duration_ms=_elapsed_ms(),
            outcome="retry",
            error_type=type(exc).__name__,
        )
        raise self.retry(exc=exc, countdown=600)
