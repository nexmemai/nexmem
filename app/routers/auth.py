"""Authentication routes.

Phase 2 changes:
* Refresh tokens are persisted (hashed) so they are revocable.
* /auth/logout revokes the presented refresh token (current session).
* /auth/logout-all revokes every refresh token for the user.
* /auth/refresh now requires the presented refresh token to be active in
  the database (or the demo store), otherwise it returns 401.
* Demo mode now has a coherent in-memory user / api-key / refresh-token
  store (``app/core/demo_auth.py``) so the integration test suite can
  exercise the full auth flow without Postgres.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.brute_force import check_not_locked, clear_failures, record_failure
from app.core import demo_auth
from app.core.deps import get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_api_key,
    get_password_hash,
    hash_refresh_token,
    verify_password,
)
from app.database import get_db
from app.models.auth import RefreshToken
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


def _client_meta(request: Request) -> tuple[Optional[str], Optional[str]]:
    user_agent = request.headers.get("User-Agent")
    ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host
    return user_agent, ip or None


async def _persist_refresh_token(
    db: Optional[AsyncSession],
    user_id: uuid.UUID,
    raw_token: str,
    request: Request,
) -> None:
    expires_at = datetime.utcnow() + timedelta(
        days=settings.refresh_token_expire_days
    )
    token_hash = hash_refresh_token(raw_token)
    if settings.demo_mode:
        demo_auth.add_refresh_token(user_id, token_hash, expires_at)
        return
    user_agent, ip = _client_meta(request)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip,
        )
    )
    await db.commit()


def _issue_token_pair(user_id: uuid.UUID) -> tuple[str, str]:
    access = create_access_token(subject=str(user_id))
    refresh = create_refresh_token(subject=str(user_id))
    return access, refresh


# ── /register ────────────────────────────────────────────────────────────────
@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
):
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

    hashed_password = (
        get_password_hash(user_data.password) if user_data.password else None
    )

    if settings.demo_mode:
        if user_data.email and demo_auth.get_user_by_email(user_data.email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )
        if user_data.wallet_address and demo_auth.get_user_by_wallet(
            user_data.wallet_address
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Wallet address already registered",
            )
        new_user = demo_auth.create_user(
            email=user_data.email,
            wallet_address=user_data.wallet_address,
            hashed_password=hashed_password,
        )
        return UserResponse(
            id=new_user.id,
            email=new_user.email,
            wallet_address=new_user.wallet_address,
            is_active=new_user.is_active,
            created_at=new_user.created_at,
        )

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


# ── /login ───────────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    if not credentials.email or not credentials.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password required",
        )

    await check_not_locked(request, credentials.email)
    INVALID = "Invalid email or password"

    if settings.demo_mode:
        user = demo_auth.get_user_by_email(credentials.email)
    else:
        result = await db.execute(
            select(User).where(User.email == credentials.email)
        )
        user = result.scalar_one_or_none()

    if not user or not user.is_active:
        await record_failure(request, credentials.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID)
    if not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account has no password. Use wallet login or API key.",
        )
    if not verify_password(credentials.password, user.hashed_password):
        await record_failure(request, credentials.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID)

    await clear_failures(credentials.email)
    access, refresh = _issue_token_pair(user.id)
    await _persist_refresh_token(db, user.id, refresh, request)

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=settings.access_token_expire_hours * 3600,
    )


# ── /refresh ─────────────────────────────────────────────────────────────────
@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a refresh token for a new access+refresh token pair.

    Phase 2: the presented refresh token must be active (row exists,
    not revoked, not expired). On success the old token is rotated.
    """
    try:
        payload = decode_token(body.refresh_token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    token_type = payload.get("type")
    if user_id is None or token_type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user ID in token"
        )

    if settings.demo_mode:
        user = demo_auth.get_user_by_id(str(user_uuid))
    else:
        user_q = await db.execute(select(User).where(User.id == user_uuid))
        user = user_q.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    token_hash = hash_refresh_token(body.refresh_token)
    if settings.demo_mode:
        if not demo_auth.is_refresh_token_active(token_hash, user_uuid):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token revoked or expired",
            )
        demo_auth.revoke_refresh_token(token_hash, user_uuid)
    else:
        result = await db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.user_id == user_uuid,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.utcnow(),
            )
        )
        stored = result.scalar_one_or_none()
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token revoked or expired",
            )
        stored.revoked_at = datetime.utcnow()
        await db.commit()

    access, new_refresh = _issue_token_pair(user_uuid)
    await _persist_refresh_token(db, user_uuid, new_refresh, request)

    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=settings.access_token_expire_hours * 3600,
    )


# ── /logout ──────────────────────────────────────────────────────────────────
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the current session's refresh token. Idempotent."""
    token_hash = hash_refresh_token(body.refresh_token)
    if settings.demo_mode:
        demo_auth.revoke_refresh_token(token_hash, current_user.id)
        return
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke every refresh token for the current user."""
    if settings.demo_mode:
        demo_auth.revoke_all_refresh_tokens(current_user.id)
        return
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()


# ── /api-keys ────────────────────────────────────────────────────────────────
@router.post(
    "/api-keys",
    response_model=APIKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    api_key_data: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raw_key, key_hash = generate_api_key()
    if settings.demo_mode:
        record = demo_auth.add_api_key(current_user.id, key_hash, api_key_data.name)
        return APIKeyCreateResponse(
            id=record.id,
            name=record.name,
            api_key=raw_key,
            scopes=record.scopes,
            created_at=record.created_at,
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
    if settings.demo_mode:
        return [
            APIKeyResponse(
                id=k.id,
                name=k.name,
                scopes=k.scopes,
                is_active=k.is_active,
                created_at=k.created_at,
                last_used_at=k.last_used_at,
            )
            for k in demo_auth.list_api_keys_for_user(current_user.id)
        ]
    result = await db.execute(select(APIKey).where(APIKey.user_id == current_user.id))
    return result.scalars().all()


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete an API key. The key stops working immediately."""
    try:
        key_uuid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid key id")
    if settings.demo_mode:
        if not demo_auth.delete_api_key(key_uuid, current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
            )
        return
    result = await db.execute(
        select(APIKey).where(APIKey.id == key_uuid, APIKey.user_id == current_user.id)
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
    return UserResponse(
        id=current_user.id,
        email=getattr(current_user, "email", None),
        wallet_address=getattr(current_user, "wallet_address", None),
        is_active=current_user.is_active,
        created_at=current_user.created_at,
    )
