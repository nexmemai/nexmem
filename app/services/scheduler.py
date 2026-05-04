"""Background task scheduler using Celery."""

import logging
from app.config import settings

logger = logging.getLogger(__name__)


def start_scheduler():
    """Start the Celery beat scheduler."""
    import subprocess
    import os

    interval_minutes = settings.consolidation_interval_minutes

    if interval_minutes <= 0:
        logger.warning("Scheduler disabled: consolidation_interval_minutes <= 0")
        return

    logger.info(f"Celery beat scheduled: consolidation every {interval_minutes} minutes")


def stop_scheduler():
    """Stop the scheduler (no-op for Celery beat)."""
    pass


def trigger_consolidation():
    """Trigger immediate consolidation via Celery task."""
    from app.tasks import consolidate_user_memory_task
    from app.database import async_session
    from sqlalchemy import select
    from app.models.user import User

    async def _trigger():
        async with async_session() as session:
            result = await session.execute(select(User.id))
            user_ids = result.scalars().all()
            for user_id in user_ids:
                consolidate_user_memory_task.delay(str(user_id))

    from asgiref.sync import async_to_sync
    async_to_sync(_trigger)()
    logger.info("Consolidation triggered for all users")
