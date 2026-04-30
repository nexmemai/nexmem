"""Procedural memory API endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.config import settings

router = APIRouter(prefix="/agents", tags=["procedural"])


class ProceduralUpsertRequest(BaseModel):
    settings_data: dict = Field(default_factory=dict, alias="settings")
    workflows: list[dict] = Field(default_factory=list)


@router.get("/{user_id}/procedural/settings")
async def get_procedural(
    user_id: str,
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get procedural memory (settings and workflows) for a user."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import get_procedural
        record = get_procedural(user_id)
        if not record:
            raise HTTPException(status_code=404, detail="No procedural memory found")
        return record

    from app.models.memory import ProceduralMemory

    query = select(ProceduralMemory).where(ProceduralMemory.user_id == str(current_user.id))
    if app_id:
        try:
            query = query.where(ProceduralMemory.app_id == uuid.UUID(app_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")

    result = await db.execute(query)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="No procedural memory found")
    return {
        "id": str(record.id),
        "user_id": str(record.user_id),
        "settings": record.settings,
        "workflows": record.workflows,
        "store_procedural": record.store_procedural,
        "updated_at": record.updated_at.isoformat(),
        "created_at": record.created_at.isoformat(),
    }


@router.post("/{user_id}/procedural/settings")
async def upsert_procedural(
    user_id: str,
    body: ProceduralUpsertRequest,
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or update procedural memory for a user."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")

    if settings.demo_mode:
        from app.demo_db import upsert_procedural as demo_upsert
        return demo_upsert(
            user_id=str(current_user.id),
            settings=body.settings_data,
            workflows=body.workflows,
        )

    from app.models.memory import ProceduralMemory

    query = select(ProceduralMemory).where(
        ProceduralMemory.user_id == str(current_user.id)
    )
    if app_id:
        try:
            query = query.where(ProceduralMemory.app_id == uuid.UUID(app_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")

    result = await db.execute(query)
    record = result.scalar_one_or_none()

    if record:
        record.settings = body.settings_data
        record.workflows = body.workflows
        record.store_procedural = True
    else:
        record = ProceduralMemory(
            user_id=str(current_user.id),
            settings=body.settings_data,
            workflows=body.workflows,
            store_procedural=True,
        )
        if app_id:
            record.app_id = uuid.UUID(app_id)
        db.add(record)

    await db.commit()
    await db.refresh(record)
    return {
        "id": str(record.id),
        "user_id": str(current_user.id),
        "upserted": True,
        "updated_at": record.updated_at.isoformat(),
    }


@router.delete("/{user_id}/procedural/settings")
async def delete_procedural(
    user_id: str,
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete procedural memory for a user."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import procedural_store
        if user_id in procedural_store:
            del procedural_store[user_id]
            return {"deleted": True, "user_id": user_id}
        raise HTTPException(status_code=404, detail="No procedural memory found")

    from app.models.memory import ProceduralMemory

    query = select(ProceduralMemory).where(
        ProceduralMemory.user_id == str(current_user.id)
    )
    if app_id:
        try:
            query = query.where(ProceduralMemory.app_id == uuid.UUID(app_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")

    result = await db.execute(query)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="No procedural memory found")
    await db.delete(record)
    await db.commit()
    return {"deleted": True, "user_id": str(current_user.id)}
