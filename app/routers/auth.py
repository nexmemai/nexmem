from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.user import User, APIKey
from app.schemas.user import (
    UserCreate, UserLogin, UserResponse,
    APIKeyCreate, APIKeyCreateResponse, APIKeyResponse,
    TokenResponse
)
from app.core.security import (
    get_password_hash, verify_password,
    create_access_token, generate_api_key
)
from app.core.deps import get_current_user

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
    await db.refresh(user)

    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db)
):
    """Login with email/password. Returns JWT token."""
    if not credentials.email or not credentials.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password required"
        )

    result = await db.execute(
        select(User).where(User.email == credentials.email)
    )
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    if not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account has no password. Use wallet login or API key."
        )

    if not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    access_token = create_access_token(subject=str(user.id))
    from app.config import settings
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_hours * 3600
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