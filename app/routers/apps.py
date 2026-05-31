"""App Management API endpoints for Multi-App Scoping."""

import logging
import uuid as _uuid
from typing import List, Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.config import settings
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.models.apps import App
from app.models.user import User
from app.services.app_quota import get_app_usage
from app.services.app_registry import (
    register_app as reg_app,
    get_user_apps,
    revoke_app as revoke_app_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps", tags=["app-management"])


def _exempt_in_demo() -> bool:
    """slowapi ``exempt_when`` for /apps/register.

    Demo mode (test suite, local dev) is exempted so unit tests that
    create many apps in a row are not throttled. Production runs
    without ``DEMO_MODE`` so the cap applies.
    """
    return bool(settings.demo_mode)


@router.post("/register", response_model=Dict[str, Any])
@limiter.limit(settings.app_register_rate_limit, exempt_when=_exempt_in_demo)
async def register_app(
    request: Request,
    app_name: str,
    description: Optional[str] = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new app for the authenticated user.

    Rate-limited per IP (P4-B3) at ``settings.app_register_rate_limit``
    (default ``10/hour``); exempt in demo mode so test suites can
    create many apps without tripping the cap.

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


# ── P4-B5 (Block 7): per-app monthly usage dashboard ─────────────────────────
@router.get("/{app_id}/usage", response_model=Dict[str, Any])
async def get_app_usage_endpoint(
    app_id: str,
    months: int = Query(
        4,
        ge=1,
        le=24,
        description="How many recent months to return (newest first)",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return monthly write / read counters for a single app.

    The caller must own the app (``apps.user_id == current_user.id``).
    The 403 vs 404 split matters: 404 hides whether a foreign app
    exists at all, which is the leakage shape we want for an
    enumeration probe.

    Demo mode returns a stable fixture when no rows exist for the
    app — the spec requires *some* shape so the dashboard renders
    in tests that have not exercised any writes / reads yet.
    """
    # Validate UUID shape early so a bogus path component does not
    # propagate to the SQL layer.
    try:
        app_uuid = _uuid.UUID(app_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid app_id")

    if settings.demo_mode:
        # In demo mode app ownership is encoded via api_keys.scopes
        # (the legacy "app:<uuid>" substring on the API key); there
        # is no apps table to consult. We fall through to the usage
        # store directly. If no rows exist, return the documented
        # static fixture so the dashboard always has something to
        # render in test runs.
        usage = await get_app_usage(db, app_uuid, months=months)
        if not usage:
            usage = [
                {
                    "month_year": "2026-05",
                    "write_count": 42,
                    "read_count": 187,
                }
            ]
        return {"app_id": str(app_uuid), "usage": usage}

    # Production: ownership check via the apps table.
    result = await db.execute(
        select(App).where(App.id == app_uuid)
    )
    app_row = result.scalar_one_or_none()
    if app_row is None:
        raise HTTPException(status_code=404, detail="App not found")
    if str(app_row.user_id) != str(current_user.id):
        # Same 404 shape so an attacker cannot enumerate apps owned
        # by other users by varying the path UUID.
        raise HTTPException(status_code=404, detail="App not found")

    usage = await get_app_usage(db, app_uuid, months=months)
    return {"app_id": str(app_uuid), "usage": usage}
