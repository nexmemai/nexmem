"""Memory router - Unified context API and episode write endpoint."""

from typing import Optional, List, Dict, Any
import asyncio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
import logging

from app.database import get_db
from app.models.user import User
from app.core.deps import get_current_user
from app.core.quotas import enforce_read_quota, enforce_write_quota
from app.core.queue_pressure import check_queue_pressure
from app.core.rate_limit import limiter
from app.core.suspension_check import check_app_not_suspended
from app.services.app_quota import record_app_read, record_app_write
from app.services.embedder import embedder
from app.services.engram_processor import engram_processor, decay_score
from app.config import settings
from app.models.memory import KnowledgeNode
from app.services.consolidation import persist_edge

router = APIRouter(prefix="/memory", tags=["memory"])
logger = logging.getLogger(__name__)


def _maybe_schedule_app_metric(
    background_tasks: BackgroundTasks,
    request: Request,
    user: User,
    *,
    is_write: bool,
) -> None:
    """Fire-and-forget app-usage increment if the request is app-bound.

    Reads ``request.state.current_app_id`` (populated by API-key auth
    in ``app.core.deps``). JWT auth leaves it None, in which case
    we record nothing — only API-key requests are bound to an app
    today (P4-B5 / Block 7). The increment helpers swallow every
    exception, so this call cannot rollback or block the response.
    """
    aid = getattr(getattr(request, "state", None), "current_app_id", None)
    if not aid:
        return
    fn = record_app_write if is_write else record_app_read
    background_tasks.add_task(fn, str(aid), str(user.id))


async def get_or_create_knowledge_node(
    db: AsyncSession,
    user_id: str,
    label: str,
    node_type: str,
    engram_id: Optional[str],
    app_id: Optional[str],
) -> tuple[str, bool]:
    """Return a knowledge node id for a label/type, creating it if needed."""
    query = select(KnowledgeNode).where(
        KnowledgeNode.user_id == user_id,
        KnowledgeNode.label == label,
        KnowledgeNode.type == node_type,
    )
    if app_id:
        query = query.where(KnowledgeNode.app_id == app_id)
    else:
        query = query.where(KnowledgeNode.app_id.is_(None))

    result = await db.execute(query.limit(1))
    node = result.scalar_one_or_none()
    if node:
        return str(node.id), False

    node = KnowledgeNode(
        user_id=user_id,
        label=label,
        type=node_type,
        properties={"source": "engram", "engram_id": engram_id},
        store_associative=True,
        app_id=app_id,
    )
    db.add(node)
    await db.flush()
    return str(node.id), True


_MAX_CONTENT = 32_768   # ~64 KB of UTF-8; matches DB CHECK constraint
_MAX_QUERY = 4_096      # context queries don't need to be longer than ~4 K chars

class ContextRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=_MAX_QUERY, description="Query to get context for")
    semantic_top_k: int = Field(5, ge=1, le=20, description="Number of semantic search results")
    episodic_limit: int = Field(5, ge=1, le=50, description="Number of recent episodes")
    max_tokens: int = Field(1200, ge=100, le=8000, description="Max context tokens")
    filters: Optional[Dict] = None
    app_id: Optional[str] = Field(None, max_length=256, description="Filter by app ID")


class ContextResponse(BaseModel):
    assembled_context: str
    engram_context: str
    semantic_hits: List[Dict[str, Any]]
    recent_episodes: List[Dict[str, Any]]
    preferences: Dict[str, Any]
    graph_context: Dict[str, Any]
    metadata: Dict[str, Any]


class EpisodeWriteRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=_MAX_CONTENT, description="Episode content")
    session_id: str = Field(..., max_length=256, description="Session identifier")
    app_id: Optional[str] = Field(None, max_length=256)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EpisodeWriteResponse(BaseModel):
    episodic_id: Optional[str]
    semantic_id: Optional[str]
    engram_id: Optional[str]
    nodes_created: int
    edges_created: int
    message: str


def assemble_context(
    engram_context: str,
    semantic_hits: List[Dict],
    recent_episodes: List[Dict],
    preferences: Dict,
    graph_context: Dict,
    max_tokens: int = 1200,
) -> str:
    """Assemble final context string for LLM injection."""
    parts = []
    tokens_used = 0

    if engram_context and tokens_used < max_tokens:
        remaining = max_tokens - tokens_used
        context_text = engram_context[: remaining * 4]
        parts.append(f"[Engram Context]\n{context_text}")
        tokens_used += len(context_text.split())

    if semantic_hits and tokens_used < max_tokens:
        parts.append("[Semantic Memory]")
        for hit in semantic_hits[:3]:
            if tokens_used >= max_tokens:
                break
            preview = hit.get("content_preview", "")[:100]
            if preview:
                parts.append(f"- {preview}")
                tokens_used += len(preview.split())

    if recent_episodes and tokens_used < max_tokens:
        parts.append("[Recent Episodes]")
        for ep in recent_episodes[:3]:
            if tokens_used >= max_tokens:
                break
            content = ep.get("content", "")[:100]
            if content:
                decay = ep.get("decay_score", 1.0)
                parts.append(f"- [{decay:.2f}] {content}")
                tokens_used += len(content.split())

    if preferences and tokens_used < max_tokens:
        settings_dict = preferences.get("settings", {})
        if settings_dict:
            settings_str = ", ".join(
                f"{k}={v}" for k, v in list(settings_dict.items())[:5]
            )
            parts.append(f"[Preferences]\n{settings_str}")
            tokens_used += len(settings_str.split())

    if graph_context and tokens_used < max_tokens:
        entities = graph_context.get("entities", [])[:5]
        if entities:
            parts.append(f"[Related Entities]\n{', '.join(entities)}")

    return "\n\n".join(parts) if parts else "[No relevant context found]"


@router.post("/context", response_model=ContextResponse, dependencies=[Depends(enforce_read_quota)])
async def get_memory_context(
    body: ContextRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Master context assembly endpoint.
    Call this before every LLM generation.
    Returns compressed, ranked, multi-source context.
    """
    user_id = str(current_user.id)

    engram = await engram_processor.process_async(body.query, user_id)
    engram_context = engram_processor.get_compressed_context(body.query, user_id)

    query_embedding = None
    try:
        query_embedding = await embedder.embed(body.query)
    except Exception as exc:
        logger.warning("Embedding failed for context query: %s", exc)

    semantic_hits = []
    if query_embedding:
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        sql_text = """
                SELECT id, content_preview, metadata,
                       1 - (vector <=> CAST(:vec AS vector)) as similarity
                FROM semantic_memory
                WHERE user_id = :uid
        """
        params = {"vec": embedding_str, "uid": user_id, "k": body.semantic_top_k}

        # Filter by app_id if provided
        if body.app_id:
            sql_text += " AND app_id = :app_id"
            params["app_id"] = body.app_id

        sql_text += """
                ORDER BY vector <=> CAST(:vec AS vector)
                LIMIT :k
            """
        result = await db.execute(text(sql_text), params)
        rows = result.fetchall()
        semantic_hits = [
            {
                "id": str(row[0]),
                "content_preview": row[1],
                "metadata": row[2],
                "similarity": float(row[3]) if row[3] else 0.0,
            }
            for row in rows
        ]

    # Build episodic query with optional app_id filter
    episodic_sql = """
        SELECT id, content, created_at, metadata
        FROM episodic_memory
        WHERE user_id = :uid
    """
    episodic_params = {"uid": user_id, "lim": body.episodic_limit}

    # Filter by app_id if provided
    if body.app_id:
        episodic_sql += " AND app_id = :app_id"
        episodic_params["app_id"] = body.app_id

    episodic_sql += """
        ORDER BY created_at DESC
        LIMIT :lim
    """

    result = await db.execute(text(episodic_sql), episodic_params)
    rows = result.fetchall()
    recent_episodes = []
    for row in rows:
        created_at = row[2]
        decay = decay_score(created_at) if created_at else 1.0
        recent_episodes.append({
            "id": str(row[0]),
            "content": row[1],
            "created_at": created_at.isoformat() if created_at else None,
            "metadata": row[3] or {},
            "decay_score": round(decay, 3),
        })

    # Build procedural query with optional app_id filter
    proc_sql = "SELECT settings, workflows FROM procedural_memory WHERE user_id = :uid"
    proc_params = {"uid": user_id}

    # Filter by app_id if provided
    if body.app_id:
        proc_sql += " AND app_id = :app_id"
        proc_params["app_id"] = body.app_id

    result = await db.execute(text(proc_sql), proc_params)
    row = result.fetchone()
    preferences = {
        "settings": row[0] if row and row[0] else {},
        "workflows": row[1] if row and row[1] else [],
    } if row else {"settings": {}, "workflows": []}

    graph_context = engram_processor.get_graph_summary(user_id)
    graph_context["entities"] = engram.get("entities", [])

    assembled = assemble_context(
        engram_context=engram_context,
        semantic_hits=semantic_hits,
        recent_episodes=recent_episodes,
        preferences=preferences,
        graph_context=graph_context,
        max_tokens=body.max_tokens,
    )

    token_count = len(assembled.split())

    # P4-B5 (Block 7): fire-and-forget app-usage read counter.
    # Recorded after the heavy retrieval succeeded so a 5xx during
    # context assembly does not get counted as a billable read.
    _maybe_schedule_app_metric(
        background_tasks, request, current_user, is_write=False
    )

    return ContextResponse(
        assembled_context=assembled,
        engram_context=engram_context,
        semantic_hits=semantic_hits,
        recent_episodes=recent_episodes,
        preferences=preferences,
        graph_context=graph_context,
        metadata={
            "engram_id": engram["engram_id"],
            "compression_ratio": engram["compression_ratio"],
            "sources_used": 5,
            "total_tokens": token_count,
        },
    )


@router.post(
    "/episode/write",
    response_model=EpisodeWriteResponse,
    dependencies=[
        # Order matters here:
        #   1. Backpressure (cheap LLEN; cuts off load before any
        #      per-user quota math runs when Celery is saturated).
        #   2. Suspension (cheap PK lookup; skips the expensive
        #      embed/engram precompute below for a banned app).
        #   3. Quota (Redis incr; the per-user monthly cap).
        # All three are non-state-changing on a 4xx so the order
        # only affects which error message the client sees first.
        Depends(check_queue_pressure),
        Depends(check_app_not_suspended),
        Depends(enforce_write_quota),
    ],
)
@limiter.limit(settings.episode_write_rate_limit)
async def write_episode(
    request: Request,
    response: Response,
    body: EpisodeWriteRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unified write endpoint.

    Phase 2 (R-105): the production path now wraps every DB write in a
    single transaction. NLP / embedding work happens BEFORE the
    transaction opens so we never hold a transaction open during slow
    CPU work. If any insert fails the transaction is rolled back and
    the client gets HTTP 500 — there are no orphan rows.
    """
    user_id = str(current_user.id)

    # ── Demo mode ────────────────────────────────────────────────────
    if settings.demo_mode:
        from app.demo_db import create_episodic, create_semantic
        from app.services.embedder import embedder

        episodic_result = create_episodic(
            user_id=user_id,
            session_id=body.session_id,
            content=body.content,
            metadata=body.metadata,
            tags=body.tags,
        )
        episodic_id = episodic_result.get("id")

        try:
            embedding = await embedder.embed(body.content)
        except Exception:
            embedding = embedder.random_vector()
        semantic_result = create_semantic(
            user_id=user_id,
            episodic_id=episodic_id,
            vector=embedding,
            summary=body.content[:200],
            content_preview=body.content[:500],
            metadata=body.metadata,
        )
        try:
            engram = await engram_processor.process_async(body.content, user_id)
            engram_id = engram.get("engram_id")
        except Exception as e:
            logger.warning(f"Engram processing failed (demo): {e}")
            engram_id = None

        _maybe_schedule_app_metric(
            background_tasks, request, current_user, is_write=True
        )

        return EpisodeWriteResponse(
            episodic_id=episodic_id,
            semantic_id=semantic_result.get("id"),
            engram_id=engram_id,
            nodes_created=0,
            edges_created=0,
            message="Episode stored successfully (demo mode)",
        )

    # ── Production mode ──────────────────────────────────────────────
    # Step 1: precompute everything that needs CPU/network work BEFORE
    # opening a transaction. If any of this fails we abort with a 502
    # and never touch the database.
    try:
        embedding = await embedder.embed(body.content)
    except Exception as exc:
        # P7-E9: log the internal cause; respond with a generic 502.
        logger.warning("Embedding precompute failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502, detail="Embedding service unavailable"
        )
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    try:
        engram = await engram_processor.process_async(body.content, user_id)
    except Exception as exc:
        # P7-E9: log internal cause; generic 502 to client.
        logger.warning("Engram precompute failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502, detail="Engram processing unavailable"
        )

    engram_id = engram.get("engram_id")
    dense_embedding = engram.get("dense_embedding") or []
    engram_vec = (
        "[" + ",".join(str(x) for x in dense_embedding) + "]"
        if dense_embedding
        else None
    )

    # Step 2: open a single transaction for every DB write. Any failure
    # rolls back the whole chain so we never end up with an episodic row
    # whose semantic / engram / graph rows are missing.
    nodes_created = 0
    edges_created = 0
    episodic_id: Optional[str] = None
    semantic_id: Optional[str] = None

    try:
        async with db.begin():
            # episodic
            result = await db.execute(
                text(
                    """
                    INSERT INTO episodic_memory
                        (user_id, session_id, content, metadata, tags, app_id)
                    VALUES (:uid, :session, :content, :meta, :tags, :app_id)
                    RETURNING id
                    """
                ),
                {
                    "uid": user_id,
                    "session": body.session_id,
                    "content": body.content,
                    "meta": body.metadata,
                    "tags": body.tags,
                    "app_id": body.app_id,
                },
            )
            row = result.fetchone()
            episodic_id = str(row[0]) if row else None
            if episodic_id is None:
                raise RuntimeError("episodic_memory insert returned no id")

            # semantic
            result = await db.execute(
                text(
                    """
                    INSERT INTO semantic_memory
                        (user_id, episodic_id, vector, content_preview, metadata, app_id)
                    VALUES (:uid, :epi_id, CAST(:vec AS vector), :preview, :meta, :app_id)
                    RETURNING id
                    """
                ),
                {
                    "uid": user_id,
                    "epi_id": episodic_id,
                    "vec": embedding_str,
                    "preview": body.content[:200],
                    "meta": body.metadata,
                    "app_id": body.app_id,
                },
            )
            row = result.fetchone()
            semantic_id = str(row[0]) if row else None

            # engram
            await db.execute(
                text(
                    """
                    INSERT INTO engrams (user_id, engram_id, distilled_text, dense_embedding,
                                         actions, objects, entities, negated_actions,
                                         salience_scores, connections, original_length,
                                         compressed_length, compression_ratio, source_type)
                    VALUES (:uid, :eid, :text, CAST(:emb AS vector), :actions, :objects,
                            :entities, :neg_actions, :salience, :conn, :orig_len, :comp_len,
                            :ratio, 'episodic')
                    """
                ),
                {
                    "uid": user_id,
                    "eid": engram_id,
                    "text": engram.get("distilled_text", ""),
                    "emb": engram_vec,
                    "actions": engram.get("actions", []),
                    "objects": engram.get("objects", []),
                    "entities": engram.get("entities", []),
                    "neg_actions": engram.get("negated_actions", []),
                    "salience": engram.get("salience_scores", {}),
                    "conn": engram.get("connections", []),
                    "orig_len": engram.get("original_length", 0),
                    "comp_len": engram.get("compressed_length", 0),
                    "ratio": engram.get("compression_ratio", 0.0),
                },
            )

            # graph nodes + edges
            node_ids: Dict[tuple[str, str], str] = {}
            for graph_edge in engram.get("graph_edges", []):
                for label_key, type_key in (
                    ("source", "source_type"),
                    ("target", "target_type"),
                ):
                    label = graph_edge.get(label_key)
                    node_type = graph_edge.get(type_key)
                    if not label or not node_type:
                        continue
                    key = (label, node_type)
                    if key in node_ids:
                        continue
                    node_id, was_created = await get_or_create_knowledge_node(
                        db, user_id, label, node_type, engram_id, body.app_id
                    )
                    node_ids[key] = node_id
                    if was_created:
                        nodes_created += 1

            for graph_edge in engram.get("graph_edges", []):
                source_key = (
                    graph_edge.get("source"),
                    graph_edge.get("source_type"),
                )
                target_key = (
                    graph_edge.get("target"),
                    graph_edge.get("target_type"),
                )
                source_id = node_ids.get(source_key)
                target_id = node_ids.get(target_key)
                if not source_id or not target_id or source_id == target_id:
                    continue
                await persist_edge(
                    db,
                    source_id=source_id,
                    target_id=target_id,
                    relation=graph_edge["relation"],
                    weight=graph_edge["weight"],
                    user_id=user_id,
                    app_id=body.app_id,
                )
                edges_created += 1
    except Exception as exc:
        # P7-E9: full traceback goes to logs / Sentry; client gets a
        # generic 500 so we never leak SQLAlchemy / asyncpg internals.
        logger.exception("episode write transaction failed; rolled back")
        raise HTTPException(
            status_code=500,
            detail="Episode write failed; no partial state was persisted",
        )

    _maybe_schedule_app_metric(
        background_tasks, request, current_user, is_write=True
    )

    return EpisodeWriteResponse(
        episodic_id=episodic_id,
        semantic_id=semantic_id,
        engram_id=engram_id,
        nodes_created=nodes_created,
        edges_created=edges_created,
        message=(
            f"Stored in all memory sources. {nodes_created} nodes and "
            f"{edges_created} edges extracted."
        ),
    )


@router.get("/engram/{engram_id}")
async def get_engram(
    engram_id: str,
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific engram by ID."""
    sql_text = "SELECT * FROM engrams WHERE user_id = :uid AND engram_id = :eid"
    params = {"uid": str(current_user.id), "eid": engram_id}

    # Filter by app_id if provided
    if app_id:
        sql_text += " AND app_id = :app_id"
        params["app_id"] = app_id

    result = await db.execute(text(sql_text), params)
    row = result.fetchone()
    if not row:
        return {"error": "Engram not found"}

    columns = result.keys()
    return dict(zip(columns, row))


@router.post("/consolidate", response_model=Dict[str, Any])
async def trigger_consolidation(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually trigger memory consolidation for the current user.
    Processes unconsolidated episodic memories older than 1 day.
    """
    from app.tasks import consolidate_user_memory_task
    
    # Task 3.1: Dispatch to Celery background worker instead of blocking main thread
    task = consolidate_user_memory_task.delay(user_id=str(current_user.id), days_old=1)
    
    return {
        "status": "queued", 
        "user_id": str(current_user.id),
        "task_id": task.id,
        "message": "Consolidation task has been queued in the background."
    }
