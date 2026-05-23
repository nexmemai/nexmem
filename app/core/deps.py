"""Auth dependency.

Phase 2 changes:
* Uses ``app.core.security.decode_token`` so JWT algorithm is whitelisted.
  ``alg=none`` is no longer accepted via fallthrough.
* Does NOT mutate the global RLS contextvar. The HTTP middleware in
  ``app/main.py`` owns the contextvar lifecycle. We only set
  ``request.state.current_user_id`` and apply RLS to the active session
  via ``set_rls_context`` so Postgres policies see the right identity
  for this request only.
* Demo mode supports multi-user auth via ``app.core.demo_auth`` so the
  test suite can exercise register/login/me with distinct user ids.
  The legacy ``DEMO_USER_ID`` short-circuit is kept as a fallback when
  no Authorization header is presented and the request reaches a route
  that does not require auth-by-default.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import demo_auth
from app.core.security import decode_token
from app.database import get_db, set_rls_context
from app.models.user import APIKey, User


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the authenticated user from a Bearer JWT or ApiKey header."""
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

    user: Optional[User] = None
    app_id_from_key: Optional[str] = None  # P4-B4 / Amendment 1: from api_keys.app_id

    if scheme.lower() == "apikey":
        key_hash = hashlib.sha256(credentials.encode()).hexdigest()
        if settings.demo_mode:
            api_key = demo_auth.get_api_key_by_hash(key_hash)
            if api_key is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
                )
            demo_user = demo_auth.get_user_by_id(str(api_key.user_id))
            if demo_user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
                )
            user = User(
                id=demo_user.id,
                email=demo_user.email,
                wallet_address=demo_user.wallet_address,
                hashed_password=demo_user.hashed_password,
                is_active=demo_user.is_active,
                created_at=demo_user.created_at,
                email_verified_at=demo_user.email_verified_at,
            )
            # Demo store does not persist app_id today; leave None so
            # the RLS policy sees the legacy "no app context" behaviour.
            app_id_from_key = getattr(api_key, "app_id", None)
        else:
            result = await db.execute(select(APIKey).where(APIKey.key_hash == key_hash))
            api_key_obj = result.scalar_one_or_none()
            if not api_key_obj or not api_key_obj.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
                )
            api_key_obj.last_used_at = datetime.utcnow()
            # Phase 4 / Amendment 1: capture the app binding (may be NULL
            # for keys not yet rotated to first-class apps). Propagated
            # below to request.state.current_app_id and to set_rls_context.
            app_id_from_key = (
                str(api_key_obj.app_id) if api_key_obj.app_id is not None else None
            )
            result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
            user = result.scalar_one_or_none()

    elif scheme.lower() == "bearer":
        try:
            payload = decode_token(credentials)
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id: str | None = payload.get("sub")
        token_type: str = payload.get("type", "access")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )
        if token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type"
            )

        try:
            user_uuid = uuid.UUID(user_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid user ID in token",
            )

        if settings.demo_mode:
            demo_user = demo_auth.get_user_by_id(str(user_uuid))
            if demo_user is None:
                # Token was issued for a user that no longer exists in the
                # demo store (e.g. between test runs that reset state).
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found or inactive",
                )
            user = User(
                id=demo_user.id,
                email=demo_user.email,
                wallet_address=demo_user.wallet_address,
                hashed_password=demo_user.hashed_password,
                is_active=demo_user.is_active,
                created_at=demo_user.created_at,
                email_verified_at=demo_user.email_verified_at,
            )
        else:
            result = await db.execute(select(User).where(User.id == user_uuid))
            user = result.scalar_one_or_none()

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported authentication scheme",
            headers={"WWW-Authenticate": "Bearer, ApiKey"},
        )

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    request.state.current_user_id = str(user.id)
    # Phase 4 / Amendment 1: stash app_id on request state so get_db
    # can pass it to set_rls_context. Only the API-key auth path
    # populates this today; the JWT path leaves it None which yields
    # the documented "rows with NULL app_id are visible regardless of
    # current_app_id" behaviour from migration 019.
    request.state.current_app_id = app_id_from_key
    if not settings.demo_mode:
        await set_rls_context(db, str(user.id), app_id_from_key)
    return user
