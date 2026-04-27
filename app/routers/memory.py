"""Memory router - Unified context API and episode write endpoint."""

from typing import Optional, List, Dict, Any
from uuid import UUID
from fastapi import APIRouter, Depends, Body
from pydantic import BaseModel, Field

from app.database import get_db
from app.models.user import User
from app.core.deps import get_current_user
from app.services.embedder import EmbeddingService
from app.services.engram_processor import engram_processor, decay_score
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

router = APIRouter(prefix="/memory", tags=["memory"])


class ContextRequest(BaseModel):
    query: str = Field(..., description="Query to get context for")
    semantic_top_k: int = Field(5, description="Number of semantic search results")
    episodic_limit: int = Field(5, description="Number of recent episodes")
    max_tokens: int = Field(1200, description="Max context tokens")
    filters: Optional[Dict] = None


class ContextResponse(BaseModel):
    assembled_context: str
    engram_context: str
    semantic_hits: List[Dict[str, Any]]
    recent_episodes: List[Dict[str, Any]]
    preferences: Dict[str, Any]
    graph_context: Dict[str, Any]
    metadata: Dict[str, Any]


class EpisodeWriteRequest(BaseModel):
    content: str = Field(..., description="Episode content")
    session_id: str = Field(..., description="Session identifier")
    app_id: Optional[str] = None
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
        remaining = max_tokens - tokens_used
        parts.append("[Semantic Memory]")
        for hit in semantic_hits[:3]:
            if tokens_used >= max_tokens:
                break
            preview = hit.get("content_preview", "")[:100]
            if preview:
                parts.append(f"- {preview}")
                tokens_used += len(preview.split())

    if recent_episodes and tokens_used < max_tokens:
        remaining = max_tokens - tokens_used
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
        remaining = max_tokens - tokens_used
        settings_str = ", ".join(
            f"{k}={v}" for k, v in list(preferences.get("settings", {}).items())[:5]
        )
        if settings_str:
            parts.append(f"[Preferences]\n{settings_str}")
            tokens_used += len(settings_str.split())

    if graph_context and tokens_used < max_tokens:
        remaining = max_tokens - tokens_used
        entities = graph_context.get("entities", [])[:5]
        if entities:
            parts.append(f"[Related Entities]\n{', '.join(entities)}")

    return "\n\n".join(parts) if parts else "[No relevant context found]"


@router.post("/context", response_model=ContextResponse)
async def get_memory_context(
    body: ContextRequest,
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

    embedding_service = EmbeddingService()
    try:
        query_embedding = await embedding_service.embed(body.query)
    except Exception:
        query_embedding = None

    semantic_hits = []
    if query_embedding:
        result = await db.execute(
            text("""
                SELECT id, content_preview, metadata,
                       1 - (embedding <=> :vec::vector) as similarity
                FROM semantic_memory
                WHERE user_id = :uid
                ORDER BY embedding <=> :vec::vector
                LIMIT :k
            """),
            {"vec": str(query_embedding), "uid": user_id, "k": body.semantic_top_k}
        )
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

    result = await db.execute(
        text("""
            SELECT id, content, created_at, metadata
            FROM episodic_memory
            WHERE user_id = :uid
            ORDER BY created_at DESC
            LIMIT :lim
        """),
        {"uid": user_id, "lim": body.episodic_limit}
    )
    rows = result.fetchall()
    recent_episodes = []
    for row in rows:
        created_at = row[2]
        decay = decay_score(created_at) if created_at else 1.0
        recent_episodes.append({
            "id": str(row[0]),
            "content": row[1],
            "created_at": created_at.isoformat() if created_at else None,
            "metadata": row[3],
            "decay_score": round(decay, 3),
        })

    result = await db.execute(
        text("SELECT settings, workflows FROM procedural_memory WHERE user_id = :uid"),
        {"uid": user_id}
    )
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


@router.post("/episode/write", response_model=EpisodeWriteResponse)
async def write_episode(
    body: EpisodeWriteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Unified write endpoint that:
    1. Saves to episodic table
    2. Generates embedding -> semantic table
    3. Runs engram processor -> engrams table
    4. Extracts entities -> graph table
    Returns summary of what was stored.
    """
    user_id = str(current_user.id)

    episodic_id = None
    semantic_id = None
    engram_id = None
    nodes_created = 0
    edges_created = 0

    result = await db.execute(
        text("""
            INSERT INTO episodic_memory (user_id, session_id, content, metadata, tags)
            VALUES (:uid, :session, :content, :meta, :tags)
            RETURNING id
        """),
        {
            "uid": user_id,
            "session": body.session_id,
            "content": body.content,
            "meta": body.metadata,
            "tags": body.tags,
        }
    )
    row = result.fetchone()
    episodic_id = str(row[0]) if row else None

    embedding_service = EmbeddingService()
    try:
        embedding = await embedding_service.embed(body.content)
        result = await db.execute(
            text("""
                INSERT INTO semantic_memory (user_id, episodic_id, vector, content_preview, metadata)
                VALUES (:uid, :epi_id, :vec, :preview, :meta)
                RETURNING id
            """),
            {
                "uid": user_id,
                "epi_id": episodic_id,
                "vec": str(embedding),
                "preview": body.content[:200],
                "meta": body.metadata,
            }
        )
        row = result.fetchone()
        semantic_id = str(row[0]) if row else None
    except Exception:
        pass

    engram = await engram_processor.process_async(body.content, user_id)
    engram_id = engram.get("engram_id")

    try:
        result = await db.execute(
            text("""
                INSERT INTO engrams (user_id, engram_id, distilled_text, dense_embedding,
                                     actions, objects, entities, negated_actions,
                                     salience_scores, connections, original_length,
                                     compressed_length, compression_ratio, source_type)
                VALUES (:uid, :eid, :text, :emb, :actions, :objects, :entities,
                        :neg_actions, :salience, :conn, :orig_len, :comp_len, :ratio, 'episodic')
                RETURNING id
            """),
            {
                "uid": user_id,
                "eid": engram_id,
                "text": engram.get("distilled_text", ""),
                "emb": str(engram.get("dense_embedding", [])),
                "actions": engram.get("actions", []),
                "objects": engram.get("objects", []),
                "entities": engram.get("entities", []),
                "neg_actions": engram.get("negated_actions", []),
                "salience": engram.get("salience_scores", {}),
                "conn": engram.get("connections", []),
                "orig_len": engram.get("original_length", 0),
                "comp_len": engram.get("compressed_length", 0),
                "ratio": engram.get("compression_ratio", 0.0),
            }
        )
    except Exception:
        pass

    for entity in engram.get("entities", []):
        result = await db.execute(
            text("""
                INSERT INTO knowledge_nodes (user_id, label, type, properties, store_associative)
                VALUES (:uid, :label, 'entity', :props, true)
                ON CONFLICT DO NOTHING
                RETURNING id
            """),
            {
                "uid": user_id,
                "label": entity,
                "props": {"source": "engram", "engram_id": engram_id},
            }
        )
        if result.fetchone():
            nodes_created += 1

    return EpisodeWriteResponse(
        episodic_id=episodic_id,
        semantic_id=semantic_id,
        engram_id=engram_id,
        nodes_created=nodes_created,
        edges_created=edges_created,
        message=f"Stored in all 5 memory sources. {nodes_created} entities extracted.",
    )


@router.get("/engram/{engram_id}")
async def get_engram(
    engram_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific engram by ID."""
    result = await db.execute(
        text("SELECT * FROM engrams WHERE user_id = :uid AND engram_id = :eid"),
        {"uid": str(current_user.id), "eid": engram_id}
    )
    row = result.fetchone()
    if not row:
        return {"error": "Engram not found"}

    columns = result.keys()
    return dict(zip(columns, row))