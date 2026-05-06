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


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Get the current authenticated user supporting both JWT (Bearer) and API keys (ApiKey).
    """
    if settings.demo_mode:
        return User(
            id=uuid.UUID(DEMO_USER_ID),
            is_active=True,
            created_at=datetime.utcnow(),
        )

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        scheme, credentials = auth_header.split()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if scheme.lower() == "apikey":
        # Handle API Key
        import hashlib
        key_hash = hashlib.sha256(credentials.encode()).hexdigest()
        result = await db.execute(select(APIKey).where(APIKey.key_hash == key_hash))
        api_key_obj = result.scalar_one_or_none()
        
        if not api_key_obj or not api_key_obj.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
            
        api_key_obj.last_used_at = datetime.utcnow()
        await db.commit()
        
        result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
        user = result.scalar_one_or_none()
        
    elif scheme.lower() == "bearer":
        # Handle JWT
        try:
            payload = jwt.decode(
                credentials, settings.secret_key, algorithms=[security.ALGORITHM]
            )
            user_id: str = payload.get("sub")
            token_type: str = payload.get("type", "access")
            
            if user_id is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
            if token_type != "access":
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
                
            try:
                user_uuid = uuid.UUID(user_id)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user ID in token")
                
            result = await db.execute(select(User).where(User.id == user_uuid))
            user = result.scalar_one_or_none()
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported authentication scheme",
            headers={"WWW-Authenticate": "Bearer, ApiKey"},
        )

    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    request.state.current_user_id = str(user.id)
    set_current_user_id(str(user.id))
    await set_rls_context(db, str(user.id))
    return user
