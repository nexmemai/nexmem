"""AI Memory Layer — FastAPI Backend

Supports two modes:
1. Demo mode (default): Uses in-memory storage for quick testing
2. Production mode: Uses PostgreSQL with pgvector
"""

from app.config import settings
from app.routers import episodic, semantic, procedural, graph, rag, auth, health, memory, apps, gdpr
from app.core.rate_limit import limiter
from app.middleware.body_size_limit import BodySizeLimitMiddleware
from app.middleware.json_shape_guard import JsonShapeGuardMiddleware
from app.middleware.read_only_mode import ReadOnlyModeMiddleware
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.core.logging import configure_logging

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException
from app.core.deps import get_current_user
from app.core import security
from app.database import (
    reset_current_user_id,
    set_current_user_id,
    reset_current_app_id,
    set_current_app_id,
)
from app.models.user import User
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError
import time
import logging
import uuid
import sentry_sdk
from prometheus_fastapi_instrumentator import Instrumentator

# Configure JSON logging once at startup
configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — runs on startup and shutdown.

    Phase 2 (R-106, R-107):
    * validate_production() RAISES on insecure config so the process
      dies at boot instead of silently running insecure.
    * The web service is configured to run with --workers 1 in
      render.yaml because the in-process NetworkX graph is not shared
      across workers. If the worker count is bumped without making
      that state shared, requests will get inconsistent graph views.
    """
    settings.validate_production()

    if settings.sentry_dsn:
        # Conservative trace + profile sampling for beta. PII is
        # scrubbed in before_send below.
        def _scrub(event, hint):
            req = event.get("request") or {}
            headers = req.get("headers") or {}
            for h in list(headers):
                if h.lower() in ("authorization", "cookie", "set-cookie", "x-api-key"):
                    headers[h] = "[redacted]"
            data = req.get("data")
            if isinstance(data, dict):
                for k in list(data):
                    if k.lower() in ("password", "refresh_token", "access_token", "api_key"):
                        data[k] = "[redacted]"
            return event

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            profiles_sample_rate=settings.sentry_profiles_sample_rate,
            environment=settings.environment,
            send_default_pii=False,
            before_send=_scrub,
        )
        logger.info("Sentry integration initialized.")
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
        import asyncio
        print("Starting with PostgreSQL connection...")
        from app.database import engine

        # Start consolidation scheduler
        from app.services.scheduler import start_scheduler
        start_scheduler()

        # Wrap startup tasks in a background task so the server binds the port
        # immediately. The graph rebuild is kicked off asynchronously after startup.
        import asyncio

        async def _background_rebuild():
            """Rebuild NetworkX graph without blocking port binding."""
            try:
                await asyncio.wait_for(rebuild_networkx_graph(), timeout=120)
                logger.info("Background graph rebuild complete.")
            except asyncio.TimeoutError:
                logger.warning("Background graph rebuild timed out after 120s – skipped.")
            except Exception as exc:
                logger.warning("Background graph rebuild failed – skipped: %s", exc)

        asyncio.create_task(_background_rebuild())

        try:
            # Verify vector dimension matches expected 384D
            from sqlalchemy import text
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("""
                    SELECT atttypmod
                    FROM pg_attribute
                    WHERE attrelid = 'semantic_memory'::regclass
                      AND attname = 'vector'
                    """)
                )
                dim_raw = result.scalar()
                dim = dim_raw if dim_raw is not None else None
                if dim != 384:
                    logger.warning(
                        "Vector dimension mismatch: expected 384, got %s", dim
                    )
        except Exception as exc:
            logger.warning("Vector dimension check failed – skipping: %s", exc)

    yield

    if not settings.demo_mode:
        from app.services.scheduler import stop_scheduler
        stop_scheduler()
        from app.database import engine
        await engine.dispose()


app = FastAPI(
    title="NexMem - Decentralized AI Memory Layer",
    description="A persistent, cross-platform memory system for AI agents and LLMs",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting middleware (60 req/min per IP by default, using Redis if available)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# P9-G1: read-only kill switch. Added BEFORE the body cap so the
# body cap ends up OUTERMOST: a 1 GB DoS payload should still 413,
# even when read-only mode would have 503'd it. Order matters here:
# Starlette inserts at position 0, so the most-recently-added
# middleware is the outermost wrapper.
app.add_middleware(
    ReadOnlyModeMiddleware,
    is_read_only=lambda: bool(settings.read_only),
)

# P7-E6: bound JSON request shape (depth + total node count). Runs
# AFTER the read-only switch and BEFORE the body cap is added below
# so a malformed JSON body during a frozen-write window 503s rather
# than 400s. Skips GET/HEAD/OPTIONS and non-JSON content types.
app.add_middleware(
    JsonShapeGuardMiddleware,
    max_depth=lambda: settings.max_request_json_depth,
    max_nodes=lambda: settings.max_request_json_nodes,
)

# P7-E5: cap request bodies. Added LAST (so OUTERMOST) — it must run
# before any inner middleware tries to read the body, before the
# kill switch (so 413 wins over 503), and before slowapi counts the
# request against the rate limit. ``max_bytes`` is a callable so
# the cap can be changed at runtime (env var + reload) without
# redeploying middleware code.
app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=lambda: settings.max_request_body_bytes,
)

# Task 4.2: Prometheus metrics — instrument only (no auto-expose).
# Metrics are served via /metrics with token auth — see endpoint below.
_instrumentator = Instrumentator().instrument(app)

# CORS middleware for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def rebuild_networkx_graph() -> None:
    """Rebuild the in-memory NetworkX graph from persisted knowledge_edges."""
    from app.database import async_session
    from app.models.memory import KnowledgeEdge, KnowledgeNode
    from app.models.user import User
    from app.services.engram_processor import engram_processor
    from sqlalchemy import select, text
    from sqlalchemy.orm import aliased

    SourceNode = aliased(KnowledgeNode)
    TargetNode = aliased(KnowledgeNode)

    async with async_session() as session:
        users = (await session.execute(select(User.id))).scalars().all()
        loaded = 0
        for user_id in users:
            await session.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_id)},
            )
            # Phase 4 / Amendment 1 — set NULL app context for the
            # startup graph rebuild. Edges are user-scoped today; the
            # NULL app id makes migration-019 policy see all rows
            # regardless of app_id (NULL-app_id rows are universally
            # visible; non-NULL rows still match because we are only
            # filtering by user_id below).
            await session.execute(
                text("SELECT set_config('app.current_app_id', :aid, true)"),
                {"aid": ""},
            )
            result = await session.execute(
                select(KnowledgeEdge, SourceNode, TargetNode)
                .join(SourceNode, KnowledgeEdge.from_node_id == SourceNode.id)
                .join(TargetNode, KnowledgeEdge.to_node_id == TargetNode.id)
                .where(KnowledgeEdge.user_id == user_id)
            )
            for edge, source, target in result.all():
                engram_processor.load_graph_edge(
                    user_id=str(edge.user_id),
                    source=source.label,
                    target=target.label,
                    relation=edge.relation,
                    weight=edge.weight,
                    source_type=source.type,
                    target_type=target.type,
                )
                loaded += 1
        logger.info("Rebuilt NetworkX graph from %s persisted edges", loaded)


from app.middleware.logging import logging_middleware


@app.middleware("http")
async def user_context_middleware(request: Request, call_next):
    """Set request-local user id so DB sessions can apply PostgreSQL RLS.

    Phase 3 hardening: routes through ``security.decode_token`` which
    whitelists ``HS256`` and rejects ``alg=none`` and other surprises.
    The previous implementation called ``jwt.decode`` directly with
    ``algorithms=[ALGORITHM]``, which was correct but inconsistent
    with the rest of the auth path; centralising on one helper means
    a future algorithm rotation only needs to touch one file.

    P3-A5: the decoded payload is stashed on ``request.state`` so
    routes can read the ``jti`` to revoke "this exact token" when
    rotating credentials. The middleware does NOT raise on a
    blocklisted/invalid token here — that decision belongs to
    ``get_current_user`` which has the full HTTP-401 contract. The
    middleware only sets the RLS context when the token decoded
    cleanly (no JWTError), so a blocklisted token never grants RLS
    visibility.
    """
    user_id = None
    auth_header = request.headers.get("Authorization")
    if auth_header:
        try:
            scheme, credentials = auth_header.split()
            if scheme.lower() == "bearer":
                payload = security.decode_token(credentials)
                # Only access tokens populate request-scoped RLS.
                # Refresh tokens go through /auth/refresh and never
                # reach this code path with a valid scheme.
                if payload.get("type", "access") == "access":
                    user_id = payload.get("sub")
                    request.state.access_token_payload = payload
        except (ValueError, JWTError):
            user_id = None

    request.state.current_user_id = user_id
    # Phase 4 / Amendment 1 — JWT auth does NOT carry an app binding
    # today, so the contextvar stays None here. The API-key auth path
    # in app/core/deps.py overwrites this on request.state when it
    # resolves an api_keys row with a non-NULL app_id. Setting None
    # here is the safe default: migration-019 policy treats NULL
    # current_app_id as "show NULL-app_id rows", which is the legacy
    # behaviour for users who never adopted multi-app scoping.
    request.state.current_app_id = None
    user_token = set_current_user_id(user_id)
    app_token = set_current_app_id(None)
    try:
        return await call_next(request)
    finally:
        reset_current_app_id(app_token)
        reset_current_user_id(user_token)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all HTTP requests with JSON formatting."""
    return await logging_middleware(request, call_next)

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
app.include_router(gdpr.router, prefix="/api/v1")


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    """Root endpoint — supports HEAD for health checks."""
    return {
        "service": "NexMem - Decentralized AI Memory Layer",
        "version": "0.1.0",
        "mode": "demo" if settings.demo_mode else "production",
    }


@app.get("/metrics")
async def metrics(request: Request):
    """
    Prometheus metrics endpoint \u2014 protected by METRICS_SECRET_KEY.

    Set METRICS_SECRET_KEY in Render env vars, then call with:
        Authorization: Bearer <METRICS_SECRET_KEY>

    If METRICS_SECRET_KEY is not configured, the endpoint returns 503.
    """
    secret = getattr(settings, "metrics_secret_key", None)
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Metrics endpoint is disabled (METRICS_SECRET_KEY not set).",
        )

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth.removeprefix("Bearer ").strip() != secret:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing metrics token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# NOTE: /health/live and /health/ready are handled by health.router
# Do NOT add a duplicate @app.get("/health") here.


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
