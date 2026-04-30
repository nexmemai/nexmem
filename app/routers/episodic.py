"""Episodic memory API endpoints."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from app.database import get_db
from app.config import settings
from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/agents", tags=["episodic"])


class EpisodeCreateRequest(BaseModel):
    session_id: str
    content: str
    timestamp: Optional[datetime] = None
    metadata: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    store_episodic: bool = True


@router.post("/{user_id}/episodes", response_model=dict)
async def create_episode(
    user_id: str,
    body: EpisodeCreateRequest,
    store_episodic: bool = True,
    app_id: Optional[str] = Query(default=None, description="App ID for scoping"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new episodic memory entry."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    effective_store = body.store_episodic and store_episodic
    if not effective_store:
        return {"id": None, "skipped": True, "reason": "store_episodic=false"}
    
    if settings.demo_mode:
        from app.demo_db import create_episodic as demo_create
        return demo_create(
            user_id=str(current_user.id),
            session_id=body.session_id,
            content=body.content,
            metadata=body.metadata,
            tags=body.tags,
            store_episodic=effective_store,
        )
    
    from app.models.memory import EpisodicMemory
    
    record = EpisodicMemory(
        user_id=str(current_user.id),
        session_id=body.session_id,
        timestamp=body.timestamp or datetime.utcnow(),
        content=body.content,
        extra_metadata=body.metadata,
        tags=body.tags,
        store_episodic=effective_store,
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
    return {
        "id": str(record.id),
        "user_id": str(current_user.id),
        "timestamp": record.timestamp.isoformat(),
        "created_at": record.created_at.isoformat(),
    }


@router.get("/{user_id}/episodes", response_model=list)
async def list_episodes(
    user_id: str,
    session_id: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    limit: int = Query(default=50, ge=1, le=200),
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List episodic memories for a user."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import get_episodic
        return get_episodic(user_id, limit=limit, session_id=session_id)

    from app.models.memory import EpisodicMemory
    
    query = (
        select(EpisodicMemory)
        .where(EpisodicMemory.user_id == str(current_user.id))
        .where(EpisodicMemory.store_episodic == True)
    )
    
    # Filter by app_id if provided
    if app_id:
        try:
            query = query.where(EpisodicMemory.app_id == UUID(app_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")
    
    if session_id:
        query = query.where(EpisodicMemory.session_id == session_id)
    if since:
        query = query.where(EpisodicMemory.timestamp >= since)
    query = query.order_by(desc(EpisodicMemory.timestamp)).limit(limit)

    result = await db.execute(query)
    records = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "user_id": str(r.user_id),
            "session_id": r.session_id,
            "timestamp": r.timestamp.isoformat(),
            "content": r.content,
            "metadata": r.extra_metadata,
            "tags": r.tags or [],
            "store_episodic": r.store_episodic,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


@router.delete("/{user_id}/episodes/{episode_id}")
async def delete_episode(
    user_id: str,
    episode_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific episodic memory."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import delete_episodic as demo_delete
        if not demo_delete(user_id, episode_id):
            raise HTTPException(status_code=404, detail="Episode not found")
        return {"deleted": True, "id": episode_id}

    from app.models.memory import EpisodicMemory
    
    result = await db.execute(
        select(EpisodicMemory).where(
            EpisodicMemory.id == episode_id,
            EpisodicMemory.user_id == str(current_user.id),
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Episode not found")
    await db.delete(record)
    await db.commit()
    return {"deleted": True, "id": episode_id}


@router.get("/{user_id}/episodes/count")
async def count_episodes(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Count episodic memories for a user."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import count_episodic
        return {"user_id": user_id, "count": count_episodic(user_id)}

    from app.models.memory import EpisodicMemory
    
    result = await db.execute(
        select(func.count()).where(
            EpisodicMemory.user_id == str(current_user.id),
            EpisodicMemory.store_episodic == True,
        )
    )
    count = result.scalar()
    return {"user_id": user_id, "count": count}


@router.post("/{user_id}/consolidate")
async def trigger_consolidation(
    user_id: str,
    days_old: int = Query(default=1, ge=1, le=30),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger memory consolidation for a user."""
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import get_episodes_to_consolidate, consolidate_episode_demo
        episodes = get_episodes_to_consolidate(user_id, days_old)
        consolidated_count = 0
        for episode in episodes:
            if consolidate_episode_demo(episode):
                consolidated_count += 1
        return {"consolidated": consolidated_count, "status": "success", "user_id": user_id}
    
    from app.services.consolidation import consolidate_for_user
    from app.services.embedder import embedder
    from app.services.llm import llm_service
    from app.services.engram_processor import engram_processor
    
    count = await consolidate_for_user(
        db, user_id, embedder, llm_service, engram_processor, days_old
    )
    return {"consolidated": count, "status": "success", "user_id": user_id}
