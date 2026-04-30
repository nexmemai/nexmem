"""AI Memory Layer — FastAPI Backend

Supports two modes:
1. Demo mode (default): Uses in-memory storage for quick testing
2. Production mode: Uses PostgreSQL with pgvector
"""

from app.config import settings
from app.routers import episodic, semantic, procedural, graph, rag, auth, health, memory, apps
from app.core.rate_limit import RateLimitMiddleware

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException
from app.core.deps import get_current_user
from app.models.user import User
from fastapi.middleware.cors import CORSMiddleware
import time
import logging
import uuid

logger = logging.getLogger("ai_memory")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — runs on startup and shutdown."""
    settings.validate_production()
    if settings.demo_mode:
        print("=" * 60)
        print("AI Memory Layer - DEMO MODE")
        print("=" * 60)
        print("Using in-memory storage (no PostgreSQL required)")
        print("Demo data initialized for user: demo_user")
        print("=" * 60)

        # Initialize demo data
        from app.demo_db import initialize_demo_data
        stats = initialize_demo_data("demo_user")
        print(f"Demo data loaded:")
        print(f"  - Episodic: {stats['episodic_count']} memories")
        print(f"  - Semantic: {stats['semantic_count']} embeddings")
        print(f"  - Procedural: {stats['procedural_count']} entries")
        print(f"  - Graph nodes: {stats['graph_node_count']}")
        print(f"  - Graph edges: {stats['graph_edge_count']}")
        print("=" * 60)
    else:
        print("Starting with PostgreSQL connection...")
        from app.database import engine

        # Start consolidation scheduler
        from app.services.scheduler import start_scheduler
        start_scheduler()

    yield

    if not settings.demo_mode:
        from app.services.scheduler import stop_scheduler
        stop_scheduler()
        from app.database import engine
        await engine.dispose()


app = FastAPI(
    title="Decentralized AI Memory Layer",
    description="A persistent, cross-platform memory system for AI agents and LLMs",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting middleware (60 req/min per IP)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60)

# CORS middleware for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all HTTP requests with method, path, status, and duration."""
    request_id = str(uuid.uuid4())[:8]
    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000)
    logger.info(
        f"[{request_id}] {request.method} {request.url.path} "
        f"-> {response.status_code} ({duration_ms}ms)"
    )
    return response

# Include routers
app.include_router(episodic.router, prefix="/api/v1")
app.include_router(semantic.router, prefix="/api/v1")
app.include_router(procedural.router, prefix="/api/v1")
app.include_router(graph.router, prefix="/api/v1")
app.include_router(rag.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(health.router)
app.include_router(memory.router, prefix="/api/v1")
app.include_router(apps.router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Decentralized AI Memory Layer",
        "version": "0.1.0",
        "mode": "demo" if settings.demo_mode else "production",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "ai-memory-layer",
        "version": "0.1.0",
        "mode": "demo" if settings.demo_mode else "production",
    }


@app.post("/api/v1/memory/cleanup")
async def trigger_cleanup(current_user: User = Depends(get_current_user)):
    """Manually trigger episodic memory cleanup."""
    if settings.demo_mode:
        return {
            "status": "ok",
            "deleted_count": 0,
            "message": "Demo mode: cleanup not needed",
        }

    from app.database import async_session
    from sqlalchemy import text

    async with async_session() as session:
        result = await session.execute(text("SELECT cleanup_expired_episodic_memory()"))
        deleted_count = result.scalar()
        return {
            "status": "ok",
            "deleted_count": deleted_count,
            "message": f"Cleaned up {deleted_count} expired episodic memories",
        }


@app.get("/api/v1/memory/stats/{user_id}")
async def get_memory_stats(
    user_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get memory statistics for a user."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import get_memory_stats
        return get_memory_stats(str(current_user.id))

    from app.database import async_session
    from sqlalchemy import text

    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT
                    (SELECT COUNT(*) FROM episodic_memory WHERE user_id = :uid) as episodic,
                    (SELECT COUNT(*) FROM semantic_memory WHERE user_id = :uid) as semantic,
                    (SELECT COUNT(*) FROM procedural_memory WHERE user_id = :uid) as procedural,
                    (SELECT COUNT(*) FROM knowledge_nodes WHERE user_id = :uid) as graph_nodes,
                    (SELECT COUNT(*) FROM knowledge_edges WHERE user_id = :uid) as graph_edges
            """),
            {"uid": str(current_user.id)}
        )
        row = result.fetchone()
        return {
            "user_id": str(current_user.id),
            "episodic_count": row[0] or 0,
            "semantic_count": row[1] or 0,
            "procedural_count": row[2] or 0,
            "graph_node_count": row[3] or 0,
            "graph_edge_count": row[4] or 0,
            "total_memories": (row[0] or 0) + (row[1] or 0) + (row[2] or 0) + (row[3] or 0),
        }


@app.get("/api/v1/memory/recent/{user_id}")
async def get_recent_memories(
    user_id: str,
    limit: int = 10,
    current_user: User = Depends(get_current_user),
):
    """Get recent memories for the live feed."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import get_recent_memories
        return get_recent_memories(str(current_user.id), limit)

    from app.database import async_session
    from sqlalchemy import text

    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT memory_type, id, text_content, created_at
                FROM recent_memories
                WHERE user_id = :uid
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {"uid": str(current_user.id), "lim": limit}
        )
        rows = result.fetchall()
        return [
            {
                "memory_type": row[0],
                "id": str(row[1]),
                "content": row[2][:200] if row[2] else "",
                "created_at": row[3].isoformat() if row[3] else None,
            }
            for row in rows
        ]


@app.post("/api/v1/demo/reset")
async def reset_demo(current_user: User = Depends(get_current_user)):
    """Reset demo data."""
    if not settings.demo_mode:
        return {"error": "Only available in demo mode"}

    from app.demo_db import initialize_demo_data

    # Clear existing data
    from app.demo_db import (
        episodic_store, semantic_store, procedural_store,
        graph_nodes_store, graph_edges_store
    )
    episodic_store.clear()
    semantic_store.clear()
    procedural_store.clear()
    graph_nodes_store.clear()
    graph_edges_store.clear()

    stats = initialize_demo_data("demo_user")
    return {"status": "ok", "message": "Demo data reset", "stats": stats}
