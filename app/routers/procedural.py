"""Procedural memory API endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import settings

router = APIRouter(prefix="/agents", tags=["procedural"])


@router.get("/{user_id}/procedural/settings")
async def get_procedural(
    user_id: str,
    current_user: User = Depends(get_current_user),
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

    from app.database import get_db
    from app.models.memory import ProceduralMemory
    from sqlalchemy import select

    async for db in get_db():
        result = await db.execute(
            select(ProceduralMemory).where(ProceduralMemory.user_id == str(current_user.id))
        )
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
    user_id_body: Optional[str] = None,
    settings_data: dict = {},
    workflows: list[dict] = [],
):
    """Create or update procedural memory for a user."""
    if settings.demo_mode:
        from app.demo_db import upsert_procedural as demo_upsert
        return demo_upsert(user_id=user_id, settings=settings_data, workflows=workflows)

    from app.database import get_db
    from app.models.memory import ProceduralMemory
    from sqlalchemy import select

    async for db in get_db():
        result = await db.execute(
            select(ProceduralMemory).where(ProceduralMemory.user_id == user_id)
        )
        record = result.scalar_one_or_none()

        if record:
            record.settings = settings_data
            record.workflows = workflows
            record.store_procedural = True
        else:
            record = ProceduralMemory(
                user_id=user_id,
                settings=settings_data,
                workflows=workflows,
                store_procedural=True,
            )
            db.add(record)

        await db.commit()
        await db.refresh(record)
        return {
            "id": str(record.id),
            "user_id": user_id,
            "upserted": True,
            "updated_at": record.updated_at.isoformat(),
        }


@router.delete("/{user_id}/procedural/settings")
async def delete_procedural(
    user_id: str,
    current_user: User = Depends(get_current_user),
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

    from app.database import get_db
    from app.models.memory import ProceduralMemory
    from sqlalchemy import select

    async for db in get_db():
        result = await db.execute(
            select(ProceduralMemory).where(
                ProceduralMemory.user_id == str(current_user.id)
            )
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="No procedural memory found")
        await db.delete(record)
        await db.commit()
        return {"deleted": True, "user_id": str(current_user.id)}
        raise HTTPException(status_code=404, detail="No procedural memory found")

    from app.database import get_db
    from app.models.memory import ProceduralMemory
    from sqlalchemy import select

    async for db in get_db():
        result = await db.execute(
            select(ProceduralMemory).where(ProceduralMemory.user_id == user_id)
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="No procedural memory found")
        await db.delete(record)
        await db.commit()
        return {"deleted": True, "user_id": user_id}
