"""TOTP / 2FA endpoints (P3-A6, Block 5).

Four endpoints, all under the ``/auth/totp`` prefix:

* ``POST /auth/totp/setup``           — JWT auth required. Generates a
  fresh secret + QR code. ``totp_enabled`` STAYS False: the user is
  not yet protected, and the secret is only stored as a candidate.
* ``POST /auth/totp/verify``          — JWT auth required. Checks a
  6-digit code against the candidate secret. On success
  ``totp_enabled`` flips to True and the next ``/auth/login`` will
  challenge for a code.
* ``POST /auth/totp/disable``         — JWT auth required. Requires
  BOTH the current password AND a valid TOTP code so a stolen access
  token alone cannot turn off 2FA.
* ``POST /auth/totp/complete-login``  — NO JWT required. Takes the
  ``totp_session_token`` minted by ``/auth/login`` plus a 6-digit
  code; on success returns a normal access + refresh token pair.

Demo-mode posture: every endpoint has a parallel demo branch that
operates on the in-memory ``demo_auth`` store. The TOTP secret is
generated and verified for real (via ``pyotp``) so the test suite
can drive the full flow deterministically — sentinel-based
"verify always succeeds" was rejected because
``test_totp_complete_login_fails_with_wrong_code`` requires real
fail-on-wrong-code behaviour.

QR code rendering: the ``otpauth://`` URI is rendered to PNG via
``qrcode[pil]`` and base64-encoded so a SPA can ``<img src="data:..."
/>`` it directly without a second round trip. The PNG is also
streamed-buffered (``io.BytesIO``) so we do not hit disk.

Security notes:
* The secret is never returned again after ``/auth/totp/setup``. A
  user who loses their authenticator app must call ``/disable`` (with
  password + code) and re-enroll, or use a server-side recovery flow
  (out of scope for Block 5).
* ``decode_totp_session_token`` checks scope, type, signature, and
  expiry. It does NOT consult the access-token blocklist (the
  blocklist is for ``type=access`` tokens; TOTP session tokens are
  ``type=totp_pending`` and intentionally orthogonal).
* No Redis dependency, so this surface does not expand R-301
  (Redis fail-open) — the JWT signature check is enough.
"""

from __future__ import annotations

import base64
import io
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import demo_auth
from app.core.audit_log import record_auth_event
from app.core.deps import get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_totp_session_token,
    generate_totp_secret,
    generate_totp_uri,
    hash_refresh_token,
    verify_password,
    verify_totp_code,
)
from app.database import get_db
from app.models.auth import RefreshToken
from app.models.user import User
from app.schemas.user import TokenResponse


logger = logging.getLogger(__name__)

# Mounted under ``/api/v1`` in app.main, matching the rest of the auth
# surface. The user-facing path is ``/api/v1/auth/totp/...``.
router = APIRouter(prefix="/auth/totp", tags=["auth"])


# ── Schemas ──────────────────────────────────────────────────────────────────
class TOTPSetupResponse(BaseModel):
    """Returned exactly once by ``POST /auth/totp/setup``."""

    secret: str = Field(..., description="Base32 RFC 6238 secret. Save this.")
    otpauth_uri: str = Field(..., description="otpauth:// provisioning URI.")
    qr_code_base64: str = Field(
        ..., description="Base64-encoded PNG QR code of otpauth_uri."
    )


class TOTPVerifyRequest(BaseModel):
    totp_code: str = Field(..., min_length=6, max_length=10)


class TOTPDisableRequest(BaseModel):
    totp_code: str = Field(..., min_length=6, max_length=10)
    password: str = Field(..., min_length=1, max_length=256)


class TOTPCompleteLoginRequest(BaseModel):
    totp_session_token: str = Field(..., min_length=20, max_length=2048)
    totp_code: str = Field(..., min_length=6, max_length=10)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _render_qr_b64(otpauth_uri: str) -> str:
    """Render the ``otpauth://`` URI as a base64-encoded PNG."""
    import qrcode  # noqa: WPS433  (lazy import keeps cold-import light)

    img = qrcode.make(otpauth_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def _persist_refresh_token_for_login(
    db: Optional[AsyncSession],
    user_id: uuid.UUID,
    raw_token: str,
    request: Request,
) -> None:
    """Persist a freshly issued refresh token after a TOTP login completes.

    Mirrors ``app.routers.auth._persist_refresh_token`` but kept
    inline so the totp router does not import private symbols from
    auth.py and create a circular dep.
    """
    expires_at = datetime.utcnow() + timedelta(
        days=settings.refresh_token_expire_days
    )
    token_hash = hash_refresh_token(raw_token)
    user_agent = request.headers.get("User-Agent")
    ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host
    if settings.demo_mode:
        demo_auth.add_refresh_token(
            user_id, token_hash, expires_at,
            user_agent=user_agent, ip_address=ip or None,
        )
        return
    from app.database import set_rls_context

    await set_rls_context(db, str(user_id))
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip or None,
        )
    )
    await db.commit()


# ── /setup ───────────────────────────────────────────────────────────────────
@router.post("/setup", response_model=TOTPSetupResponse)
async def totp_setup(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a fresh TOTP secret and store it as a candidate.

    ``totp_enabled`` stays False until ``/auth/totp/verify`` succeeds;
    until then the user can still log in with email + password alone.
    Calling ``/setup`` twice rotates the secret — the previous one is
    overwritten. This is intentional: a user who lost their setup
    midway should be able to start over.
    """
    secret = generate_totp_secret()
    otpauth_uri = generate_totp_uri(secret, current_user.email or "user")
    qr_b64 = _render_qr_b64(otpauth_uri)

    if settings.demo_mode:
        demo_auth.set_totp_secret(current_user.id, secret)
    else:
        from app.database import set_rls_context

        await set_rls_context(db, str(current_user.id))
        await db.execute(
            update(User)
            .where(User.id == current_user.id)
            .values(totp_secret=secret)
        )
        await db.commit()

    await record_auth_event(
        "totp_setup",
        target_user_id=current_user.id,
        request=request,
    )

    return TOTPSetupResponse(
        secret=secret,
        otpauth_uri=otpauth_uri,
        qr_code_base64=qr_b64,
    )


# ── /verify ──────────────────────────────────────────────────────────────────
@router.post("/verify", status_code=status.HTTP_200_OK)
async def totp_verify(
    body: TOTPVerifyRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify a TOTP code against the stored secret and enable 2FA.

    Returns ``{"enabled": true}`` on success. On failure raises 400
    with a generic message — never leaks whether the secret existed
    or the code was malformed vs wrong.
    """
    if settings.demo_mode:
        demo_user = demo_auth.get_user_by_id(str(current_user.id))
        secret = demo_user.totp_secret if demo_user else None
    else:
        secret = current_user.totp_secret

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP setup has not been started",
        )

    if not verify_totp_code(secret, body.totp_code):
        await record_auth_event(
            "totp_verify_failure",
            target_user_id=current_user.id,
            request=request,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code",
        )

    if settings.demo_mode:
        demo_auth.enable_totp(current_user.id)
    else:
        from app.database import set_rls_context

        await set_rls_context(db, str(current_user.id))
        await db.execute(
            update(User)
            .where(User.id == current_user.id)
            .values(totp_enabled=True)
        )
        await db.commit()

    await record_auth_event(
        "totp_enabled",
        target_user_id=current_user.id,
        request=request,
    )
    return {"enabled": True}


# ── /disable ─────────────────────────────────────────────────────────────────
@router.post("/disable", status_code=status.HTTP_200_OK)
async def totp_disable(
    body: TOTPDisableRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable TOTP. Requires BOTH the password AND a current code.

    Two-factor disablement requires both factors so a leaked access
    token alone is not enough. Returns ``{"disabled": true}`` on
    success; on any mismatch returns a generic 400 so the caller
    cannot tell which factor failed.
    """
    GENERIC = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid credentials",
    )

    if settings.demo_mode:
        demo_user = demo_auth.get_user_by_id(str(current_user.id))
        if demo_user is None:
            raise GENERIC
        if not demo_user.hashed_password or not verify_password(
            body.password, demo_user.hashed_password
        ):
            raise GENERIC
        secret = demo_user.totp_secret
    else:
        if not current_user.hashed_password or not verify_password(
            body.password, current_user.hashed_password
        ):
            raise GENERIC
        secret = current_user.totp_secret

    if not secret or not verify_totp_code(secret, body.totp_code):
        raise GENERIC

    if settings.demo_mode:
        demo_auth.disable_totp(current_user.id)
    else:
        from app.database import set_rls_context

        await set_rls_context(db, str(current_user.id))
        await db.execute(
            update(User)
            .where(User.id == current_user.id)
            .values(totp_enabled=False, totp_secret=None)
        )
        await db.commit()

    await record_auth_event(
        "totp_disabled",
        target_user_id=current_user.id,
        request=request,
    )
    return {"disabled": True}


# ── /complete-login ──────────────────────────────────────────────────────────
@router.post("/complete-login", response_model=TokenResponse)
async def totp_complete_login(
    body: TOTPCompleteLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Trade a totp_session_token + 6-digit code for full tokens.

    Called by the client after ``/auth/login`` returned
    ``{requires_totp: true, totp_session_token: "..."}``. Failures
    (expired session token, wrong scope, wrong code, missing user)
    all 401 with a generic message.
    """
    GENERIC_401 = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired TOTP session",
    )

    try:
        payload = decode_totp_session_token(body.totp_session_token)
    except JWTError:
        raise GENERIC_401

    sub = payload.get("sub")
    if not sub:
        raise GENERIC_401
    try:
        user_uuid = uuid.UUID(sub)
    except ValueError:
        raise GENERIC_401

    if settings.demo_mode:
        demo_user = demo_auth.get_user_by_id(str(user_uuid))
        if demo_user is None or not demo_user.is_active:
            raise GENERIC_401
        if not demo_user.totp_enabled:
            raise GENERIC_401
        secret = demo_user.totp_secret
    else:
        result = await db.execute(select(User).where(User.id == user_uuid))
        user_row = result.scalar_one_or_none()
        if user_row is None or not user_row.is_active:
            raise GENERIC_401
        if not user_row.totp_enabled:
            raise GENERIC_401
        secret = user_row.totp_secret

    if not secret or not verify_totp_code(secret, body.totp_code):
        await record_auth_event(
            "login_totp_failure",
            target_user_id=user_uuid,
            request=request,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid TOTP code",
        )

    access = create_access_token(subject=str(user_uuid))
    refresh = create_refresh_token(subject=str(user_uuid))
    await _persist_refresh_token_for_login(db, user_uuid, refresh, request)
    await record_auth_event(
        "login_totp_success",
        target_user_id=user_uuid,
        request=request,
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=settings.access_token_expire_hours * 3600,
    )
