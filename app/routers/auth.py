from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import async_session, get_db, set_auth_lookup_context, set_rls_context
from app.config import settings
from app.models.user import User, APIKey
from app.schemas.user import (
    UserCreate, UserLogin, UserResponse,
    APIKeyCreate, APIKeyCreateResponse, APIKeyResponse,
    TokenResponse, RefreshRequest
)
from app.core.security import (
    get_password_hash, verify_password,
    create_access_token, create_refresh_token, generate_api_key,
    ALGORITHM
)
from jose import jwt, JWTError
from app.core.deps import get_current_user
from app.core.brute_force import check_not_locked, record_failure, clear_failures
from app.core.usage_quota import get_empty_usage_quota_status, get_usage_quota_status

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
):
    """Register a new user with email/password or wallet address."""
    if not user_data.email and not user_data.wallet_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email or wallet address required"
        )
    if user_data.email and not user_data.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password required for email registration"
        )

    await set_auth_lookup_context(
        db,
        email=user_data.email,
        wallet_address=user_data.wallet_address,
    )

    if user_data.email:
        existing = await db.execute(
            select(User).where(User.email == user_data.email)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

    if user_data.wallet_address:
        existing = await db.execute(
            select(User).where(User.wallet_address == user_data.wallet_address)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Wallet address already registered"
            )

    hashed_password = None
    if user_data.password:
        hashed_password = get_password_hash(user_data.password)

    user = User(
        email=user_data.email,
        wallet_address=user_data.wallet_address,
        hashed_password=hashed_password,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await set_rls_context(db, str(user.id))
    await db.refresh(user)

    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    """Login with email/password. Returns JWT token."""
    if not credentials.email or not credentials.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password required",
        )

    # ── Brute-force check (before DB query to fail fast) ──────────────────────
    await check_not_locked(request, credentials.email)

    # ── Lookup user ───────────────────────────────────────────────────────────
    await set_auth_lookup_context(db, email=credentials.email)
    result = await db.execute(
        select(User).where(User.email == credentials.email)
    )
    user = result.scalar_one_or_none()

    # ── Validate (always record failure, even on unknown email, to prevent
    #    user-enumeration via timing differences) ──────────────────────────────
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

    # ── Success — clear failure counter and issue token ───────────────────────
    await clear_failures(credentials.email)
    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))
    from app.config import settings
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_hours * 3600,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshRequest,
    db: AsyncSession = Depends(get_db)
):
    """Obtain a new access token using a valid refresh token."""
    from app.config import settings
    
    try:
        payload = jwt.decode(
            request.refresh_token, settings.secret_key, algorithms=[ALGORITHM]
        )
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        
        if user_id is None or token_type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        import uuid
        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user ID in token")

        await set_rls_context(db, str(user_uuid))
        result = await db.execute(select(User).where(User.id == user_uuid))
        user = result.scalar_one_or_none()
        
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
            )
            
        # Issue new tokens
        access_token = create_access_token(subject=str(user.id))
        new_refresh_token = create_refresh_token(subject=str(user.id))
        
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
            expires_in=settings.access_token_expire_hours * 3600,
        )
        
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/api-keys", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    api_key_data: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new API key for the current user. Returns raw key ONCE."""
    raw_key, key_hash = generate_api_key()

    api_key = APIKey(
        user_id=current_user.id,
        key_hash=key_hash,
        name=api_key_data.name,
        scopes="read,write",
        is_active=True,
    )
    db.add(api_key)
    await db.commit()
    await set_rls_context(db, str(current_user.id))
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
    db: AsyncSession = Depends(get_db)
):
    """List all API keys for the current user. Does NOT return raw keys."""
    result = await db.execute(
        select(APIKey).where(APIKey.user_id == current_user.id)
    )
    api_keys = result.scalars().all()
    return api_keys


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Revoke (delete) an API key."""
    result = await db.execute(
        select(APIKey).where(
            APIKey.id == key_id,
            APIKey.user_id == current_user.id
        )
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )

    await db.delete(api_key)
    await db.commit()


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """Get current user info."""
    return current_user


@router.get("/me/usage")
async def get_current_user_usage(
    current_user: User = Depends(get_current_user),
):
    """Get current user's monthly token usage quota status."""
    if settings.demo_mode:
        return get_empty_usage_quota_status(current_user)
    async with async_session() as db:
        await set_rls_context(db, str(current_user.id))
        return await get_usage_quota_status(db, current_user)
