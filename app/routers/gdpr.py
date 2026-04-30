"""GDPR data export, deletion, and consent endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.engram import Engram
from app.models.memory import (
    EpisodicMemory,
    KnowledgeEdge,
    KnowledgeNode,
    ProceduralMemory,
    SemanticMemory,
)
from app.models.user import APIKey, User

router = APIRouter(prefix="/memory/user", tags=["gdpr"])


class ConsentFlags(BaseModel):
    marketing: bool = False
    analytics: bool = True


def _json_safe(value: Any) -> Any:
    """Convert ORM values into JSON-safe primitives."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    return value


def _model_to_dict(model: Any) -> dict[str, Any]:
    """Serialize a SQLAlchemy model using database column names."""
    data = {}
    for column in model.__table__.columns:
        data[column.name] = _json_safe(getattr(model, column.key))
    return data


async def _fetch_all(db: AsyncSession, model: Any, user_id: UUID) -> list[dict[str, Any]]:
    result = await db.execute(select(model).where(model.user_id == user_id))
    return [_model_to_dict(record) for record in result.scalars().all()]


@router.get("/{user_id}/export")
async def export_all_memories(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export all user-scoped memory data for the authenticated user."""
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot export another user's data")

    episodic = await _fetch_all(db, EpisodicMemory, user_id)
    semantic = await _fetch_all(db, SemanticMemory, user_id)
    procedural = await _fetch_all(db, ProceduralMemory, user_id)
    graph_nodes = await _fetch_all(db, KnowledgeNode, user_id)
    graph_edges = await _fetch_all(db, KnowledgeEdge, user_id)
    engrams = await _fetch_all(db, Engram, user_id)

    return {
        "exported_at": datetime.utcnow().isoformat(),
        "user_id": str(user_id),
        "episodic": episodic,
        "semantic": semantic,
        "procedural": procedural,
        "graph": {"nodes": graph_nodes, "edges": graph_edges},
        "engrams": engrams,
    }


@router.delete("/{user_id}/all")
async def delete_all_memories(
    user_id: UUID,
    confirm: str | None = Header(None, alias="X-Confirm-Delete"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all memory data and invalidate authentication for the user."""
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot delete another user's data")
    if confirm != "true":
        raise HTTPException(status_code=400, detail="Send X-Confirm-Delete: true")

    delete_counts = {}
    for model in (
        Engram,
        SemanticMemory,
        EpisodicMemory,
        ProceduralMemory,
        KnowledgeEdge,
        KnowledgeNode,
    ):
        result = await db.execute(delete(model).where(model.user_id == user_id))
        delete_counts[model.__tablename__] = result.rowcount or 0

    api_key_result = await db.execute(delete(APIKey).where(APIKey.user_id == user_id))
    delete_counts["api_keys"] = api_key_result.rowcount or 0

    await db.delete(current_user)
    await db.commit()

    return {
        "deleted": True,
        "user_id": str(user_id),
        "authentication_invalidated": True,
        "deleted_counts": delete_counts,
    }


@router.patch("/{user_id}/consent")
async def update_consent(
    user_id: UUID,
    flags: ConsentFlags,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store GDPR consent flags in procedural_memory.settings['consent']."""
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot update another user's consent")

    result = await db.execute(
        select(ProceduralMemory).where(
            ProceduralMemory.user_id == user_id,
            ProceduralMemory.app_id.is_(None),
        )
    )
    procedural = result.scalar_one_or_none()

    consent = flags.model_dump()
    if procedural:
        settings = dict(procedural.settings or {})
        settings["consent"] = consent
        procedural.settings = settings
    else:
        procedural = ProceduralMemory(
            user_id=user_id,
            app_id=None,
            settings={"consent": consent},
            workflows=[],
            store_procedural=True,
        )
        db.add(procedural)

    await db.commit()
    return {
        "updated": True,
        "user_id": str(user_id),
        "consent": consent,
    }
