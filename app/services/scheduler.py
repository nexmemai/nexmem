"""Background task scheduler for memory consolidation."""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.services.consolidation import run_consolidation_all
from app.services.embedder import embedder
from app.services.llm import llm_service
from app.services.engram_processor import engram_processor
from app.database import async_session

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def scheduled_consolidation():
    """Run consolidation job based on configured interval."""
    try:
        async with async_session() as db:
            result = await run_consolidation_all(
                db, embedder, llm_service, engram_processor
            )
            logger.info(f"Consolidation complete: {result}")
    except Exception as e:
        logger.error(f"Scheduled consolidation failed: {e}")


def start_scheduler():
    """Start the background scheduler."""
    interval_minutes = settings.consolidation_interval_minutes
    
    if interval_minutes <= 0:
        logger.warning("Scheduler disabled: consolidation_interval_minutes <= 0")
        return
    
    scheduler.add_job(
        scheduled_consolidation,
        IntervalTrigger(minutes=interval_minutes),
        id="consolidation_job",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started: consolidation every {interval_minutes} minutes")


def stop_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
