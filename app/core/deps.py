from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt, JWTError
import uuid
from datetime import datetime

from app.database import get_db
from app.database import set_current_user_id, set_rls_context
from app.models.user import User, APIKey
from app.core import security
from app.config import settings
from app.demo_db import DEMO_USER_ID


async def verify_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Strict API key verification - returns 401 if not using 'ApiKey' scheme.
    Use this for endpoints that require API key auth specifically.
    """
    if settings.demo_mode:
        return User(
            id=uuid.UUID(DEMO_USER_ID),
            is_active=True,
            created_at=datetime.utcnow(),
        )
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )
    
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise credentials_exception

    try:
        scheme, credentials = auth_header.split()
    except ValueError:
        raise credentials_exception

    if scheme.lower() != "apikey":
        raise credentials_exception

    import hashlib
    key_hash = hashlib.sha256(credentials.encode()).hexdigest()
    
    result = await db.execute(select(APIKey).where(APIKey.key_hash == key_hash))
    api_key_obj = result.scalar_one_or_none()
    
    if not api_key_obj or not api_key_obj.is_active:
        raise credentials_exception
        
    api_key_obj.last_used_at = datetime.utcnow()
    await db.commit()
        
    result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
    user = result.scalar_one_or_none()
    
    if user is None or not user.is_active:
        raise credentials_exception

    request.state.current_user_id = str(user.id)
    set_current_user_id(str(user.id))
    await set_rls_context(db, str(user.id))
    return user


async def get_current_user(user: User = Depends(verify_api_key)) -> User:
    """Dependency wrapper for verify_api_key."""
    return user
