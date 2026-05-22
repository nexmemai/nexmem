"""Celery configuration for background processing.

Phase 6 hardening (P6-D2, P6-D3, P6-D4):
* ``task_soft_time_limit`` raises ``SoftTimeLimitExceeded`` inside the
  task so it can clean up partial state. ``task_time_limit`` is the
  hard kill: the worker process is replaced. Without these, a
  consolidation task that hangs on a stalled OpenAI call would pin a
  worker forever.
* ``worker_max_tasks_per_child`` recycles the worker after N tasks so
  spaCy / sentence-transformers cannot leak unbounded memory across
  consolidation runs.
* ``result_expires`` caps how long task results sit in Redis. The
  default keeps results forever and slowly fills the broker.
* ``task_acks_late`` + ``worker_prefetch_multiplier=1`` (P9-G2 prep):
  on graceful shutdown the task is re-queued instead of dropped, and
  workers do not hoard prefetched tasks they cannot finish.

Settings come from ``app.config.settings`` so an operator can tune them
per deploy without redeploying code (env vars override the defaults).
"""

import os

from celery import Celery
from celery.schedules import crontab

from app.config import settings


REDIS_URL = os.getenv("REDIS_URL", settings.redis_url or "redis://localhost:6379/0")

celery_app = Celery(
    "memory_layer_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task lifecycle
    task_track_started=True,
    task_publish_retry=True,
    # P6-D2: hard caps on every task.
    task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
    task_time_limit=settings.celery_task_time_limit_seconds,

    # P9-G2 prep: late-ack so a SIGTERM during a task re-queues it
    # rather than dropping it. ``prefetch_multiplier=1`` keeps each
    # worker's in-flight set to exactly one so the redelivery window
    # is small.
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # P6-D3 / P6-D4: bound worker memory + Redis result-set growth.
    worker_max_tasks_per_child=settings.celery_worker_max_tasks_per_child,
    result_expires=settings.celery_result_expires_seconds,

    beat_schedule={
        "consolidate-all-users-every-30-minutes": {
            "task": "app.tasks.consolidate_all_users",
            "schedule": crontab(minute="*/30"),
        },
    },
)
