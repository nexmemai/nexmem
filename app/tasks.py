"""Celery tasks for background processing."""

import logging
from asgiref.sync import async_to_sync

from app.celery_app import celery_app
from app.database import async_session
from app.services.consolidation import consolidate_for_user

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def consolidate_user_memory_task(self, user_id: str, days_old: int = 1):
    """
    Background task to consolidate episodic memories into semantic engrams and graphs.
    """
    async def _run_consolidation():
        from app.services.embedder import embedder
        from app.services.llm import LLMService
        from app.services.engram_processor import engram_processor
        
        llm = LLMService()

        async with async_session() as db:
            try:
                logger.info(f"Starting background consolidation for user: {user_id}")
                result = await consolidate_for_user(
                    db=db, 
                    user_id=user_id, 
                    embedder=embedder, 
                    llm_service=llm, 
                    engram_processor=engram_processor,
                    days_old=days_old
                )
                return result
            except Exception as e:
                raise e

    try:
        result = async_to_sync(_run_consolidation)()
        return result
    except Exception as exc:
        logger.error(f"Consolidation task failed for user {user_id}: {exc}")
        
        if self.request.retries >= self.max_retries:
            logger.critical(
                f"DLQ [DEAD LETTER QUEUE]: Task failed permanently for user {user_id} "
                f"after {self.max_retries} retries. Reason: {exc}. "
                f"Task ID: {self.request.id}"
            )
            return False
            
        countdown = 60 * (2 ** self.request.retries)
        logger.info(f"Retrying task in {countdown} seconds...")
        raise self.retry(exc=exc, countdown=countdown)


@celery_app.task(bind=True, max_retries=3)
def consolidate_all_users(self):
    """Scheduled task to consolidate all users' memories."""
    from sqlalchemy import select
    from app.models.user import User

    async def _run_all():
        async with async_session() as session:
            result = await session.execute(select(User.id))
            user_ids = result.scalars().all()
            triggered = 0
            for user_id in user_ids:
                consolidate_user_memory_task.delay(str(user_id))
                triggered += 1
            logger.info(f"Queued consolidation for {triggered} users")
            return {"users_queued": triggered}

    try:
        return async_to_sync(_run_all)()
    except Exception as exc:
        logger.error(f"Consolidate all users task failed: {exc}")
        raise self.retry(exc=exc, countdown=300)
