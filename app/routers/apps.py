"""App Management API endpoints for Multi-App Scoping."""

import logging
from typing import List, Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.config import settings
from app.core.deps import get_current_user
from app.models.user import User
from app.services.app_registry import (
    register_app as reg_app,
    get_user_apps,
    revoke_app as revoke_app_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps", tags=["app-management"])


@router.post("/register", response_model=Dict[str, Any])
async def register_app(
    app_name: str,
    description: Optional[str] = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new app for the authenticated user.
    
    Returns:
        - app_id: The unique app identifier
        - api_key: The API key to use (shown only once!)
        - name: App name
    """
    if not app_name or len(app_name.strip()) == 0:
        raise HTTPException(status_code=400, detail="app_name is required")
    
    try:
        result = await reg_app(
            db, str(current_user.id), app_name.strip(), description or ""
        )
        logger.info(f"App registered: {app_name} for user {current_user.id}")
        return result
    except Exception as e:
        logger.error(f"Failed to register app: {e}")
        raise HTTPException(status_code=500, detail="Failed to register app")


@router.get("/list", response_model=List[Dict[str, Any]])
async def list_apps(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all apps registered by the authenticated user."""
    try:
        apps = await get_user_apps(db, str(current_user.id))
        return apps
    except Exception as e:
        logger.error(f"Failed to list apps: {e}")
        raise HTTPException(status_code=500, detail="Failed to list apps")


@router.delete("/{key_id}")
async def revoke_app(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke an app's API key.
    The app will no longer be able to access the memory layer.
    """
    try:
        success = await revoke_app_service(db, key_id, str(current_user.id))
        if not success:
            raise HTTPException(status_code=404, detail="App not found")
        return {"revoked": True, "key_id": key_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to revoke app: {e}")
        raise HTTPException(status_code=500, detail="Failed to revoke app")


@router.get("/{app_id}/stats", response_model=Dict[str, Any])
async def get_app_stats(
    app_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get memory statistics for a specific app.
    Requires the app to be registered by the authenticated user.
    """
    from app.models.memory import (
        EpisodicMemory, SemanticMemory, KnowledgeNode, KnowledgeEdge,
    )
    
    # Validate app belongs to user
    from app.services.app_registry import validate_app_access
    is_valid = await validate_app_access(db, app_id, str(current_user.id))
    if not is_valid:
        raise HTTPException(status_code=403, detail="App access denied")
    
    try:
        # Get counts scoped by app_id
        episodic_count = await db.execute(
            select(func.count()).where(
                EpisodicMemory.user_id == current_user.id,
                EpisodicMemory.app_id == UUID(app_id),
            )
        )
        semantic_count = await db.execute(
            select(func.count()).where(
                SemanticMemory.user_id == current_user.id,
                SemanticMemory.app_id == UUID(app_id),
            )
        )
        node_count = await db.execute(
            select(func.count()).where(
                KnowledgeNode.user_id == current_user.id,
                KnowledgeNode.app_id == UUID(app_id),
            )
        )
        edge_count = await db.execute(
            select(func.count()).where(
                KnowledgeEdge.user_id == current_user.id,
                KnowledgeEdge.app_id == UUID(app_id),
            )
        )
        
        return {
            "app_id": app_id,
            "episodic_count": episodic_count.scalar() or 0,
            "semantic_count": semantic_count.scalar() or 0,
            "graph_node_count": node_count.scalar() or 0,
            "graph_edge_count": edge_count.scalar() or 0,
        }
    except Exception as e:
        logger.error(f"Failed to get app stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get app stats")
