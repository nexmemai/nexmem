"""Auth router.

Demo mode contract:
  When `settings.demo_mode` is true, every endpoint returns synthetic
  data without touching the database. This is required because demo
  mode is the default for tests and local development. Production
  (and integration tests) must run with `DEMO_MODE=false`.

Refresh-token revocation (R-H11):
  Real refresh tokens carry a `jti` claim that maps to a row in
  the `refresh_tokens` table. The /auth/refresh endpoint rotates
  the row (revoke old, mint new). /auth/logout revokes the supplied
  refresh token. /auth/logout-all revokes every active refresh
  token for the current user.

  Access tokens remain valid until their `exp` (default 4 h). For
  Phase 2 we accept this short window in exchange for not paying
  per-request DB lookups on access-token validation. This is
  documented in PROJECT_STATUS.md "Known limitations".
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select, update
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
from app.models.user import APIKey, RefreshToken, User
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
    refresh, _jti, _exp = create_refresh_token(subject=user_id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=settings.access_token_expire_hours * 3600,
    )


# ── Real (DB-backed) token issuance ────────────────────────────────────────


async def _issue_token_pair(
    db: AsyncSession, user_id: str, *, label: str | None = None
) -> TokenResponse:
    """Mint an access + refresh pair AND persist the refresh row.

    Used by login, refresh (rotate), and any other path that wants to
    create a new authenticated session.
    """
    access = create_access_token(subject=user_id)
    refresh, jti, expires_at = create_refresh_token(subject=user_id)

    db.add(
        RefreshToken(
            id=uuid.UUID(jti),
            user_id=uuid.UUID(user_id),
            issued_at=datetime.utcnow(),
            expires_at=expires_at,
            label=label,
        )
    )
    await db.flush()
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

    # Set the RLS GUC so the refresh_tokens INSERT satisfies the
    # `WITH CHECK (user_id = current_setting(...))` policy.
    from app.database import set_rls_context, set_current_user_id

    set_current_user_id(str(user.id))
    await set_rls_context(db, str(user.id))
    return await _issue_token_pair(db, str(user.id))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """Rotate a refresh token: revoke the consumed one, issue a fresh pair.

    Rejects with 401 if:
      - the token signature is invalid or expired (jose.JWTError)
      - the token's `type` claim is not 'refresh'
      - the token has no `jti` claim (legacy tokens issued before
        migration 014 cannot be rotated; user must log in again)
      - the corresponding refresh_tokens row is missing, revoked,
        or past `expires_at`
    """
    try:
        payload = jwt.decode(body.refresh_token, settings.secret_key, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        token_jti: str | None = payload.get("jti")
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

    if token_jti is None:
        # Pre-014 token; cannot be tracked. Force re-login.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has no jti; please log in again.",
        )

    try:
        user_uuid = uuid.UUID(user_id)
        jti_uuid = uuid.UUID(token_jti)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token ids"
        )

    # Set RLS GUC so the refresh_tokens row is visible.
    from app.database import set_rls_context, set_current_user_id

    set_current_user_id(str(user_uuid))
    await set_rls_context(db, str(user_uuid))

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.id == jti_uuid)
    )
    rt = result.scalar_one_or_none()
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found or revoked.",
        )
    if rt.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked."
        )
    if rt.expires_at <= datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired."
        )
    if str(rt.user_id) != str(user_uuid):
        # Defensive: the JWT sub claim does not match the persisted row.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token mismatch."
        )

    # Confirm user still exists & is active.
    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )

    # Rotate: revoke the consumed row and mint a new pair.
    rt.revoked_at = datetime.utcnow()
    return await _issue_token_pair(db, str(user.id), label=rt.label)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """Revoke a single refresh token.

    Idempotent: replaying the call with an already-revoked token is a 204.
    """
    if settings.demo_mode:
        return

    try:
        payload = jwt.decode(
            body.refresh_token, settings.secret_key, algorithms=[ALGORITHM]
        )
        token_type = payload.get("type")
        token_jti = payload.get("jti")
        user_id = payload.get("sub")
        if token_type != "refresh" or token_jti is None or user_id is None:
            # Not a real refresh token; nothing to revoke. Treat as success
            # (no information leak about whether the token existed).
            return
        jti_uuid = uuid.UUID(token_jti)
        user_uuid = uuid.UUID(user_id)
    except (JWTError, ValueError):
        return

    from app.database import set_rls_context, set_current_user_id

    set_current_user_id(str(user_uuid))
    await set_rls_context(db, str(user_uuid))

    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == jti_uuid, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke every active refresh token for the current user.

    Access tokens remain valid until their `exp` (max 4 h by default).
    """
    if settings.demo_mode:
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
