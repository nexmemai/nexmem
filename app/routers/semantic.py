"""Semantic memory API endpoints with vector search."""

import asyncio
import logging
from typing import Optional, List, Dict, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, text

from app.database import get_db
from app.config import settings
from app.core.deps import get_current_user
from app.core.quotas import enforce_write_quota
from app.models.user import User
from app.services.embedder import embedder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["semantic"])


class SemanticCreateRequest(BaseModel):
    content: str
    episodic_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    summary: Optional[str] = None
    embedding_model: str = "all-MiniLM-L6-v2"
    index_semantic: bool = True


class SemanticSearchRequest(BaseModel):
    query: str


@router.post("/{user_id}/semantics", response_model=dict, dependencies=[Depends(enforce_write_quota)])
async def create_semantic(
    user_id: str,
    body: SemanticCreateRequest,
    app_id: Optional[str] = Query(default=None, description="App ID for scoping"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new semantic memory entry with vector embedding."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if not body.index_semantic:
        return {"id": None, "skipped": True, "reason": "index_semantic=false"}

    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import create_semantic as demo_create
        
        try:
            vector = await asyncio.to_thread(embedder.embed, body.content)
        except Exception as e:
            logger.warning(f"Embedding failed, using random vector: {e}")
            vector = embedder.random_vector()

        return demo_create(
            user_id=str(current_user.id),
            vector=vector,
            episodic_id=body.episodic_id,
            embedding_model=body.embedding_model,
            summary=body.summary,
            content_preview=body.content[:500],
            metadata=body.metadata,
            index_semantic=body.index_semantic,
        )

    from app.models.memory import SemanticMemory
    
    try:
        vector = await asyncio.to_thread(embedder.embed, body.content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embedding service error: {str(e)}")
    
    record = SemanticMemory(
        user_id=str(current_user.id),
        episodic_id=body.episodic_id,
        vector=vector,
        embedding_model=body.embedding_model,
        summary=body.summary,
        content_preview=body.content[:500],
        extra_metadata=body.metadata,
        index_semantic=body.index_semantic,
    )
    # Add app_id if provided
    if app_id:
        try:
            record.app_id = UUID(app_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {"id": str(record.id), "user_id": str(current_user.id), "created_at": record.created_at.isoformat()}


@router.post("/{user_id}/semantic/search", response_model=List[Dict[str, Any]])
async def semantic_search(
    user_id: str,
    body: SemanticSearchRequest,
    k: int = Query(default=5, ge=1, le=50),
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search for semantically similar memories using vector similarity."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import search_semantic

        try:
            query_vector = await asyncio.to_thread(embedder.embed, body.query)
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

    try:
        query_vector = await asyncio.to_thread(embedder.embed, body.query)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embedding service error: {str(e)}")
    
    # Build SQL with optional app_id filter
    sql_text = """
        SELECT id, summary, content_preview, metadata,
               1 - (vector <=> CAST(:query_vec AS vector)) AS similarity
        FROM semantic_memory
        WHERE user_id = :uid AND index_semantic = TRUE
    """
    params = {"query_vec": str(query_vector), "uid": str(current_user.id), "k": k}
    
    if app_id:
        try:
            sql_text += " AND app_id = :app_id"
            params["app_id"] = UUID(app_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")
    
    sql_text += """
        ORDER BY vector <=> CAST(:query_vec AS vector)
        LIMIT :k
    """
    
    sql = text(sql_text)
    
    result = await db.execute(sql, params)
    rows = result.fetchall()

    return [
        {
            "id": str(row[0]),
            "summary": row[1],
            "content_preview": row[2],
            "metadata": row[3] or {},
            "similarity": round(float(row[4]), 4) if row[4] else 0.0,
        }
        for row in rows
    ]


@router.get("/{user_id}/semantics", response_model=List[Dict[str, Any]])
async def list_semantics(
    user_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List semantic memories for a user."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
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

    from app.models.memory import SemanticMemory
    
    query = (
        select(SemanticMemory)
        .where(SemanticMemory.user_id == str(current_user.id))
        .where(SemanticMemory.index_semantic == True)
    )
    
    # Filter by app_id if provided
    if app_id:
        try:
            query = query.where(SemanticMemory.app_id == UUID(app_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")
    
    query = query.order_by(desc(SemanticMemory.created_at)).limit(limit)
    result = await db.execute(query)
    records = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "user_id": str(r.user_id),
            "summary": r.summary,
            "content_preview": r.content_preview,
            "embedding_model": r.embedding_model,
            "metadata": r.extra_metadata or {},
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


@router.get("/{user_id}/semantics/count")
async def count_semantics(
    user_id: str,
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Count semantic memories for a user."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import count_semantic
        return {"user_id": user_id, "count": count_semantic(user_id)}

    from app.models.memory import SemanticMemory
    
    query = select(func.count()).where(
        SemanticMemory.user_id == str(current_user.id),
        SemanticMemory.index_semantic == True,
    )
    
    # Filter by app_id if provided
    if app_id:
        try:
            query = query.where(SemanticMemory.app_id == UUID(app_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")
    
    result = await db.execute(query)
    return {"user_id": str(current_user.id), "count": result.scalar() or 0}
