from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt, JWTError
import uuid
from datetime import datetime

from app.database import get_db
from app.models.user import User, APIKey
from app.core import security
from app.config import settings
from app.demo_db import DEMO_USER_ID

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Get the current user based on either:
    1. Bearer Token (JWT)
    2. ApiKey (mem_...)
    3. Demo mode: Return synthetic user
    """
    # Demo mode bypass: return synthetic user
    if settings.demo_mode:
        return User(
            id=uuid.UUID(DEMO_USER_ID),
            is_active=True,
            created_at=datetime.utcnow(),
        )
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise credentials_exception

    try:
        scheme, credentials = auth_header.split()
    except ValueError:
        raise credentials_exception

    if scheme.lower() == "bearer":
        # JWT verification
        try:
            payload = jwt.decode(
                credentials, settings.secret_key, algorithms=[security.ALGORITHM]
            )
            user_id: str = payload.get("sub")
            if user_id is None:
                raise credentials_exception
        except JWTError:
            raise credentials_exception
            
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise credentials_exception
        return user
        
    elif scheme.lower() == "apikey":
        # API Key verification
        # The key hash in the DB is SHA256
        import hashlib
        key_hash = hashlib.sha256(credentials.encode()).hexdigest()
        
        result = await db.execute(select(APIKey).where(APIKey.key_hash == key_hash))
        api_key_obj = result.scalar_one_or_none()
        
        if not api_key_obj or not api_key_obj.is_active:
            raise credentials_exception
            
        # Update last used
        from datetime import datetime
        api_key_obj.last_used_at = datetime.utcnow()
        await db.commit()
            
        # Get user
        result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
        user = result.scalar_one_or_none()
        
        if user is None or not user.is_active:
            raise credentials_exception
            
        return user
        
    else:
        raise credentials_exception
