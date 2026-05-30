"""Health check endpoints.

Phase 2 (R-104):
* /health/live is unchanged: minimal-logic process-alive check.
* /health/ready now also probes Redis when REDIS_URL is set, so the
  readiness contract reflects every dependency the request path
  actually requires (DB + Redis + embedding service). Each check
  reports its individual status.
* DB probe latency is measured and a slow-DB warning is included in
  the response when it crosses 1000 ms.
"""
import asyncio
import time
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness():
    """Fast liveness check - is the process alive?"""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
    }


async def _probe_redis() -> str:
    """Round-trip ping against Redis. Returns 'ok' or 'error: ...'."""
    if not settings.redis_url:
        return "skipped: REDIS_URL not configured"
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, socket_timeout=2)
        try:
            pong = await asyncio.wait_for(client.ping(), timeout=2)
            if pong is not True:
                return f"error: unexpected response {pong!r}"
            return "ok"
        finally:
            await client.aclose() if hasattr(client, "aclose") else await client.close()
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


async def _probe_database(db: AsyncSession) -> tuple[str, float]:
    """Round-trip SELECT 1 against the DB. Returns (status, latency_ms)."""
    if settings.demo_mode:
        return "skipped: demo mode", 0.0
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}", (time.perf_counter() - started) * 1000
    return "ok", (time.perf_counter() - started) * 1000


@router.get("/ready")
async def readiness(db: AsyncSession = Depends(get_db)):
    """Readiness check - are all dependencies reachable?"""
    checks: dict = {}

    db_status, db_latency_ms = await _probe_database(db)
    checks["database"] = db_status
    checks["database_latency_ms"] = round(db_latency_ms, 1)

    checks["redis"] = await _probe_redis()

    embedding_status = "ok"
    if settings.openai_api_key and settings.openai_api_key not in (
        "sk-placeholder",
        "sk-test-placeholder",
    ):
        try:
            from app.services.embedder import embedder

            test = await embedder.embed("health check")
            if len(test) == 0:
                embedding_status = "error: empty embedding"
        except Exception as exc:  # noqa: BLE001
            embedding_status = f"error: {exc}"
    else:
        embedding_status = "skipped: no API key"
    checks["embedding_service"] = embedding_status

    all_ok = all(
        v == "ok" or (isinstance(v, str) and v.startswith("skipped"))
        for k, v in checks.items()
        if isinstance(v, str)
    )
    if db_latency_ms > 1000:
        checks["warnings"] = ["database probe exceeded 1000 ms"]

    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
            "version": "0.1.0",
            "service": "NexMem",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
