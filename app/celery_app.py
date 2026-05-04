"""Celery configuration for background processing."""

import os
from celery import Celery
from celery.schedules import cr

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "memory_layer_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_publish_retry=True,
    beat_schedule={
        "consolidate-all-users-every-30-minutes": {
            "task": "app.tasks.consolidate_all_users",
            "schedule": cr(minutes=30),
        },
    },
)
