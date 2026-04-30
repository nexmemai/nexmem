from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from datetime import datetime
import asyncio

from app.database import get_db
from app.config import settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness():
    """Fast liveness check - is the process alive?"""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/ready")
async def readiness(
    db: AsyncSession = Depends(get_db)
):
    """Readiness check - are all dependencies reachable?"""
    checks = {}

    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {str(e)}"
    checks["database"] = db_status

    embedding_status = "ok"
    if settings.openai_api_key and settings.openai_api_key != "sk-placeholder":
        try:
            from app.services.embedder import EmbeddingService
            service = EmbeddingService()
            test = await asyncio.to_thread(service.embed, "health check")
            if len(test) == 0:
                embedding_status = "error: empty embedding"
        except Exception as e:
            embedding_status = f"error: {str(e)}"
    else:
        embedding_status = "skipped: no API key"
    checks["embedding_service"] = embedding_status

    all_ok = all(
        v == "ok" or "skipped" in v
        for v in checks.values()
    )
    status_code = 200 if all_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
            "version": "0.1.0",
        "service": "NexMem",
            "timestamp": datetime.utcnow().isoformat()
        }
    )
