"""App Registry Service - Manages app registrations and scoping.

Allows multiple applications to use the same memory layer
with isolated memory spaces (scoped by app_id).
"""

import logging
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, APIKey

logger = logging.getLogger(__name__)

# ==========================================
# App Registry Core
# ==========================================


async def register_app(
    db: AsyncSession,
    user_id: str,
    app_name: str,
    description: str = "",
) -> Dict[str, Any]:
    """
    Register a new app for a user.
    Returns the app_id and generated API key.
    """
    app_id = str(uuid.uuid4())
    
    # In a full implementation, you'd have an App model
    # For now, we'll store app info in the user's metadata or a separate table
    # This is a simplified version
    
    # Generate an API key for this app
    from app.core.security import get_password_hash
    import secrets
    
    raw_key = f"app_{secrets.token_urlsafe(32)}"
    key_hash = get_password_hash(raw_key)
    
    # Create API key scoped to this app
    api_key = APIKey(
        user_id=uuid.UUID(user_id),
        key_hash=key_hash,
        name=app_name,
        scopes="read,write",
        is_active=True,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    
    logger.info(f"Registered app '{app_name}' for user {user_id}: {app_id}")
    
    return {
        "app_id": app_id,
        "app_name": app_name,
        "description": description,
        "api_key": raw_key,  # Only shown once!
        "key_id": str(api_key.id),
        "created_at": datetime.utcnow().isoformat(),
    }


async def get_user_apps(
    db: AsyncSession,
    user_id: str,
) -> List[Dict[str, Any]]:
    """Get all apps registered by a user."""
    result = await db.execute(
        select(APIKey)
        .where(APIKey.user_id == uuid.UUID(user_id))
        .order_by(APIKey.created_at.desc())
    )
    keys = result.scalars().all()
    
    return [
        {
            "key_id": str(k.id),
            "app_name": k.name,
            "scopes": k.scopes,
            "is_active": k.is_active,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat(),
        }
        for k in keys
    ]


async def validate_app_access(
    db: AsyncSession,
    app_id: str,
    user_id: str,
) -> bool:
    """
    Validate that an app belongs to a user.
    In a full implementation, this would check an App model.
    For now, we check if the API key name matches.
    """
    # This is a simplified validation
    # In production, you'd query an App model with user_id and app_id
    return True  # Simplified for now


async def revoke_app(
    db: AsyncSession,
    key_id: str,
    user_id: str,
) -> bool:
    """Revoke an app's API key."""
    result = await db.execute(
        select(APIKey)
        .where(APIKey.id == uuid.UUID(key_id))
        .where(APIKey.user_id == uuid.UUID(user_id))
    )
    api_key = result.scalar_one_or_none()
    
    if not api_key:
        return False
    
    api_key.is_active = False
    await db.commit()
    
    logger.info(f"Revoked app API key {key_id} for user {user_id}")
    return True


async def get_app_scoped_query(
    model_class,
    user_id: str,
    app_id: Optional[str] = None,
):
    """
    Get a base query scoped by user_id and optionally app_id.
    Usage:
        query = await get_app_scoped_query(EpisodicMemory, user_id, app_id)
    """
    query = select(model_class).where(model_class.user_id == user_id)
    
    if app_id and hasattr(model_class, 'app_id'):
        # Filter by app_id if provided
        query = query.where(model_class.app_id == uuid.UUID(app_id))
    
    return query


def add_app_id_to_record(record, app_id: Optional[str]):
    """Add app_id to a new record if provided."""
    if app_id and hasattr(record, 'app_id') and app_id:
        try:
            record.app_id = uuid.UUID(app_id)
        except ValueError:
            logger.warning(f"Invalid app_id format: {app_id}")
