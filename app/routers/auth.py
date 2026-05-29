"""Auth router.

Demo mode contract:
  When `settings.demo_mode` is true, every endpoint returns synthetic data
  without touching the database. This is required because demo mode is the
  default for tests and local development, and the routes were previously
  always hitting Postgres (which broke CI). Production (and integration
  tests) must run with `DEMO_MODE=false`; that path is unchanged.
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.brute_force import check_not_locked, clear_failures, record_failure
from app.core.deps import get_current_user
from app.core.security import (
    ALGORITHM,
    create_access_token,
    create_refresh_token,
    generate_api_key,
    get_password_hash,
    verify_password,
)
from app.database import get_db
from app.demo_db import DEMO_USER_ID
from app.models.user import APIKey, User
from app.schemas.user import (
    APIKeyCreate,
    APIKeyCreateResponse,
    APIKeyResponse,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)


router = APIRouter(prefix="/auth", tags=["auth"])


# ── Demo helpers ────────────────────────────────────────────────────────────

def _demo_user_response(email: Optional[str] = None) -> dict:
    return {
        "id": DEMO_USER_ID,
        "email": email,
        "wallet_address": None,
        "is_active": True,
        "created_at": datetime.utcnow(),
    }


def _demo_token_response(user_id: str = DEMO_USER_ID) -> TokenResponse:
    access = create_access_token(subject=user_id)
    refresh = create_refresh_token(subject=user_id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=settings.access_token_expire_hours * 3600,
    )


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user with email/password or wallet address."""
    if not user_data.email and not user_data.wallet_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email or wallet address required",
        )
    if user_data.email and not user_data.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password required for email registration",
        )

    if settings.demo_mode:
        return _demo_user_response(email=user_data.email)

    if user_data.email:
        existing = await db.execute(select(User).where(User.email == user_data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

    if user_data.wallet_address:
        existing = await db.execute(
            select(User).where(User.wallet_address == user_data.wallet_address)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Wallet address already registered",
            )

    hashed_password = get_password_hash(user_data.password) if user_data.password else None

    user = User(
        email=user_data.email,
        wallet_address=user_data.wallet_address,
        hashed_password=hashed_password,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    """Login with email/password. Returns JWT access and refresh tokens."""
    if not credentials.email or not credentials.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password required",
        )

    if settings.demo_mode:
        # Demo: any credentials succeed and produce a JWT bound to the demo user.
        return _demo_token_response()

    await check_not_locked(request, credentials.email)

    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalar_one_or_none()

    _INVALID = "Invalid email or password"

    if not user or not user.is_active:
        await record_failure(request, credentials.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID)

    if not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account has no password. Use wallet login or API key.",
        )

    if not verify_password(credentials.password, user.hashed_password):
        await record_failure(request, credentials.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID)

    await clear_failures(credentials.email)
    return TokenResponse(
        access_token=create_access_token(subject=str(user.id)),
        refresh_token=create_refresh_token(subject=str(user.id)),
        token_type="bearer",
        expires_in=settings.access_token_expire_hours * 3600,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """Obtain a new access token using a valid refresh token."""
    try:
        payload = jwt.decode(request.refresh_token, settings.secret_key, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        if user_id is None or token_type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.demo_mode:
        return _demo_token_response(user_id=user_id)

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user ID in token"
        )

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )

    return TokenResponse(
        access_token=create_access_token(subject=str(user.id)),
        refresh_token=create_refresh_token(subject=str(user.id)),
        token_type="bearer",
        expires_in=settings.access_token_expire_hours * 3600,
    )


@router.post(
    "/api-keys", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED
)
async def create_api_key(
    api_key_data: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new API key for the current user. Returns raw key ONCE."""
    raw_key, key_hash = generate_api_key()

    if settings.demo_mode:
        return APIKeyCreateResponse(
            id=uuid.uuid4(),
            name=api_key_data.name,
            api_key=raw_key,
            scopes="read,write",
            created_at=datetime.utcnow(),
        )

    api_key = APIKey(
        user_id=current_user.id,
        key_hash=key_hash,
        name=api_key_data.name,
        scopes="read,write",
        is_active=True,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return APIKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        api_key=raw_key,
        scopes=api_key.scopes,
        created_at=api_key.created_at,
    )


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all API keys for the current user. Does NOT return raw keys."""
    if settings.demo_mode:
        return []

    result = await db.execute(select(APIKey).where(APIKey.user_id == current_user.id))
    return result.scalars().all()


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke (delete) an API key."""
    if settings.demo_mode:
        return

    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )
    await db.delete(api_key)
    await db.commit()


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user info."""
    return current_user
