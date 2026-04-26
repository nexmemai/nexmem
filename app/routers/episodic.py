"""Episodic memory API endpoints."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings

router = APIRouter(prefix="/agents", tags=["episodic"])


@router.post("/{user_id}/episodes", response_model=dict)
async def create_episode(
    user_id: str,
    session_id: str,
    content: str,
    timestamp: Optional[datetime] = None,
    metadata: dict = {},
    tags: list[str] = [],
    store_episodic: bool = True,
):
    """Create a new episodic memory entry."""
    if not store_episodic:
        return {"id": None, "skipped": True, "reason": "store_episodic=false"}

    if settings.demo_mode:
        from app.demo_db import create_episodic as demo_create
        return demo_create(
            user_id=user_id,
            session_id=session_id,
            content=content,
            metadata=metadata,
            tags=tags,
            store_episodic=store_episodic,
        )

    from app.database import get_db
    from app.models.memory import EpisodicMemory

    async for db in get_db():
        record = EpisodicMemory(
            user_id=user_id,
            session_id=session_id,
            timestamp=timestamp or datetime.utcnow(),
            content=content,
            metadata=metadata,
            tags=tags,
            store_episodic=store_episodic,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return {
            "id": str(record.id),
            "user_id": user_id,
            "timestamp": record.timestamp.isoformat(),
            "created_at": record.created_at.isoformat(),
        }


@router.get("/{user_id}/episodes", response_model=list)
async def list_episodes(
    user_id: str,
    session_id: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List episodic memories for a user."""
    if settings.demo_mode:
        from app.demo_db import get_episodic
        return get_episodic(user_id, limit=limit, session_id=session_id)

    from app.database import get_db
    from app.models.memory import EpisodicMemory
    from sqlalchemy import select, desc

    async for db in get_db():
        query = (
            select(EpisodicMemory)
            .where(EpisodicMemory.user_id == user_id)
            .where(EpisodicMemory.store_episodic == True)
        )
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
                "user_id": r.user_id,
                "session_id": r.session_id,
                "timestamp": r.timestamp.isoformat(),
                "content": r.content,
                "metadata": r.metadata,
                "tags": r.tags,
                "store_episodic": r.store_episodic,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ]


@router.delete("/{user_id}/episodes/{episode_id}")
async def delete_episode(user_id: str, episode_id: str):
    """Delete a specific episodic memory."""
    if settings.demo_mode:
        from app.demo_db import delete_episodic
        if not delete_episodic(user_id, episode_id):
            raise HTTPException(status_code=404, detail="Episode not found")
        return {"deleted": True, "id": episode_id}

    from app.database import get_db
    from app.models.memory import EpisodicMemory
    from sqlalchemy import select

    async for db in get_db():
        result = await db.execute(
            select(EpisodicMemory).where(
                EpisodicMemory.id == episode_id,
                EpisodicMemory.user_id == user_id,
            )
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Episode not found")
        await db.delete(record)
        await db.commit()
        return {"deleted": True, "id": episode_id}


@router.get("/{user_id}/episodes/count")
async def count_episodes(user_id: str):
    """Count episodic memories for a user."""
    if settings.demo_mode:
        from app.demo_db import count_episodic
        return {"user_id": user_id, "count": count_episodic(user_id)}

    from app.database import get_db
    from app.models.memory import EpisodicMemory
    from sqlalchemy import select, func

    async for db in get_db():
        result = await db.execute(
            select(func.count()).where(
                EpisodicMemory.user_id == user_id,
                EpisodicMemory.store_episodic == True,
            )
        )
        count = result.scalar()
        return {"user_id": user_id, "count": count}
