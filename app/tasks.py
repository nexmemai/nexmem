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
        
        # Instantiate LLMService if not imported as singleton
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
        # Celery is synchronous, so we bridge to our async service
        result = async_to_sync(_run_consolidation)()
        return result
    except Exception as exc:
        logger.error(f"Consolidation task failed for user {user_id}: {exc}")
        
        # Task 3.3: Dead Letter Queue (DLQ) Fallback
        if self.request.retries >= self.max_retries:
            logger.critical(
                f"DLQ [DEAD LETTER QUEUE]: Task failed permanently for user {user_id} "
                f"after {self.max_retries} retries. Reason: {exc}. "
                f"Task ID: {self.request.id}"
            )
            # In a full enterprise setup, we would insert this into a 'failed_jobs' PG table here
            return False
            
        # Task 3.1 & 3.2: Retry with exponential backoff
        countdown = 60 * (2 ** self.request.retries)
        logger.info(f"Retrying task in {countdown} seconds...")
        raise self.retry(exc=exc, countdown=countdown)
