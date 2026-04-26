"""Semantic memory API endpoints with vector search."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["semantic"])


@router.post("/{user_id}/semantics", response_model=dict)
async def create_semantic(
    user_id: str,
    content: str,
    episodic_id: Optional[str] = None,
    metadata: dict = {},
    summary: Optional[str] = None,
    embedding_model: str = "text-embedding-3-small",
    index_semantic: bool = True,
):
    """Create a new semantic memory entry with vector embedding."""
    if not index_semantic:
        return {"id": None, "skipped": True, "reason": "index_semantic=false"}

    from app.services.embedder import embedder

    if settings.demo_mode:
        from app.demo_db import create_semantic as demo_create

        try:
            vector = embedder.embed(content)
        except Exception as e:
            logger.warning(f"Embedding failed, using random vector: {e}")
            vector = embedder.random_vector()

        return demo_create(
            user_id=user_id,
            vector=vector,
            episodic_id=episodic_id,
            embedding_model=embedding_model,
            summary=summary,
            content_preview=content[:500],
            metadata=metadata,
            index_semantic=index_semantic,
        )

    from app.database import get_db
    from app.models.memory import SemanticMemory

    async for db in get_db():
        try:
            vector = embedder.embed(content)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Embedding service error: {str(e)}")

        record = SemanticMemory(
            user_id=user_id,
            episodic_id=episodic_id,
            vector=vector,
            embedding_model=embedding_model,
            summary=summary,
            content_preview=content[:500],
            metadata=metadata,
            index_semantic=index_semantic,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return {"id": str(record.id), "user_id": user_id, "created_at": record.created_at.isoformat()}


@router.post("/{user_id}/semantic/search", response_model=list)
async def semantic_search(
    user_id: str,
    query: str,
    k: int = Query(default=5, ge=1, le=50),
):
    """Search for semantically similar memories using vector similarity."""
    from app.services.embedder import embedder

    if settings.demo_mode:
        from app.demo_db import search_semantic

        try:
            query_vector = embedder.embed(query)
        except Exception as e:
            logger.warning(f"Embedding failed for search: {e}")
            query_vector = embedder.random_vector()

        results = search_semantic(user_id, query_vector, k)
        return [
            {
                "id": r["id"],
                "summary": r.get("summary"),
                "content_preview": r.get("content_preview"),
                "similarity": r.get("similarity", 0),
                "metadata": r.get("metadata", {}),
            }
            for r in results
        ]

    from app.database import get_db
    from sqlalchemy import text

    async for db in get_db():
        try:
            query_vector = embedder.embed(query)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Embedding service error: {str(e)}")

        sql = text("""
            SELECT id, summary, content_preview, metadata,
                   1 - (vector <=> :query_vec::vector) AS similarity
            FROM semantic_memory
            WHERE user_id = :uid AND index_semantic = TRUE
            ORDER BY vector <=> :query_vec::vector
            LIMIT :k
        """)

        result = await db.execute(sql, {"query_vec": str(query_vector), "uid": user_id, "k": k})
        rows = result.fetchall()

        return [
            {
                "id": row.id,
                "summary": row.summary,
                "content_preview": row.content_preview,
                "similarity": round(row.similarity, 4),
                "metadata": row.metadata or {},
            }
            for row in rows
        ]


@router.get("/{user_id}/semantics", response_model=list)
async def list_semantics(
    user_id: str,
    limit: int = Query(default=50, ge=1, le=200),
):
    """List semantic memories for a user."""
    if settings.demo_mode:
        from app.demo_db import get_semantic
        records = get_semantic(user_id, limit)
        return [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "summary": r.get("summary"),
                "content_preview": r.get("content_preview"),
                "embedding_model": r.get("embedding_model"),
                "metadata": r.get("metadata", {}),
                "created_at": r["created_at"],
            }
            for r in records
        ]

    from app.database import get_db
    from app.models.memory import SemanticMemory
    from sqlalchemy import select, desc

    async for db in get_db():
        result = await db.execute(
            select(SemanticMemory)
            .where(SemanticMemory.user_id == user_id)
            .where(SemanticMemory.index_semantic == True)
            .order_by(desc(SemanticMemory.created_at))
            .limit(limit)
        )
        records = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "user_id": r.user_id,
                "summary": r.summary,
                "content_preview": r.content_preview,
                "embedding_model": r.embedding_model,
                "metadata": r.metadata,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ]


@router.get("/{user_id}/semantics/count")
async def count_semantics(user_id: str):
    """Count semantic memories for a user."""
    if settings.demo_mode:
        from app.demo_db import count_semantic
        return {"user_id": user_id, "count": count_semantic(user_id)}

    from app.database import get_db
    from app.models.memory import SemanticMemory
    from sqlalchemy import select, func

    async for db in get_db():
        result = await db.execute(
            select(func.count()).where(
                SemanticMemory.user_id == user_id,
                SemanticMemory.index_semantic == True,
            )
        )
        count = result.scalar()
        return {"user_id": user_id, "count": count}
