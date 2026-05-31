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

Phase 3 changes (P3-A1 .. P3-A4, P3-A8):
* /auth/register issues a single-use email-verification token and is
  rate-limited to ``settings.register_rate_limit`` per IP via slowapi
  (P3-A8). Demo mode is exempted so the test suite is not throttled.
* /auth/verify-email/confirm consumes a verification token and stamps
  ``users.email_verified_at``. /auth/verify-email/resend reissues a
  token without leaking which addresses are registered.
* /auth/login refuses to mint tokens for an email-bearing user whose
  ``email_verified_at`` is unset when ``EMAIL_VERIFICATION_REQUIRED``
  is true (P3-A1).
* /auth/password-reset/request and /auth/password-reset/confirm
  implement a forgot-password flow with single-use 30-minute tokens.
  A successful confirm rotates the password and revokes every active
  refresh token for the user (P3-A2).
* /auth/change-password rotates the password from inside an
  authenticated session and revokes every refresh token (P3-A3).
* GET /auth/sessions lists the user's active refresh tokens (id,
  user_agent, ip, issued_at, expires_at). DELETE /auth/sessions/{id}
  revokes one (P3-A4).

token issuance / consumption never returns the raw verification or
reset token from production code paths to clients other than the
intended recipient. Logging is via structlog (PII redacted by the
middleware list); raw tokens are not logged.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from jose import JWTError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit_log import record_auth_event
from app.core.brute_force import check_not_locked, clear_failures, record_failure
from app.core import demo_auth
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_api_key,
    generate_url_safe_token,
    get_password_hash,
    hash_refresh_token,
    hash_url_safe_token,
    verify_password,
)
from app.database import get_db
from app.models.auth import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
)
from app.models.user import APIKey, User
from app.schemas.user import (
    APIKeyCreate,
    APIKeyCreateResponse,
    APIKeyResponse,
    EmailVerificationConfirm,
    EmailVerificationResendRequest,
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    SessionResponse,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Helpers ──────────────────────────────────────────────────────────────────
def _client_meta(request: Request) -> tuple[Optional[str], Optional[str]]:
    user_agent = request.headers.get("User-Agent")
    ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host
    return user_agent, ip or None


def _exempt_register_in_demo() -> bool:
    """slowapi ``exempt_when`` callback for ``/auth/register``.

    Demo mode (the test suite, local dev) is exempted so the unit
    suite is not throttled. Production runs without DEMO_MODE so the
    cap applies.
    """
    return bool(settings.demo_mode)


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
    user_agent, ip = _client_meta(request)
    if settings.demo_mode:
        demo_auth.add_refresh_token(
            user_id, token_hash, expires_at,
            user_agent=user_agent, ip_address=ip,
        )
        return
    # Apply RLS context to the active session so the INSERT into
    # refresh_tokens passes the WITH CHECK clause added in 013_extend_rls.
    from app.database import set_rls_context

    await set_rls_context(db, str(user_id))
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


async def _issue_email_verification_token(
    db: Optional[AsyncSession], user_id: uuid.UUID
) -> str:
    """Issue an email-verification token (P3-A1).

    Returns the raw token. The caller is responsible for delivering it
    to the user (via email in production; via the API response in
    demo / dev mode for the test suite).
    """
    raw, digest = generate_url_safe_token()
    expires_at = datetime.utcnow() + timedelta(
        hours=settings.email_verification_token_ttl_hours
    )
    if settings.demo_mode:
        demo_auth.add_email_verification_token(user_id, digest, expires_at)
    else:
        from app.database import set_rls_context

        await set_rls_context(db, str(user_id))
        db.add(
            EmailVerificationToken(
                user_id=user_id, token_hash=digest, expires_at=expires_at
            )
        )
        await db.commit()
    return raw


async def _issue_password_reset_token(
    db: Optional[AsyncSession],
    user_id: uuid.UUID,
    request: Request,
) -> str:
    """Issue a password-reset token (P3-A2)."""
    raw, digest = generate_url_safe_token()
    expires_at = datetime.utcnow() + timedelta(
        minutes=settings.password_reset_token_ttl_minutes
    )
    user_agent, ip = _client_meta(request)
    if settings.demo_mode:
        demo_auth.add_password_reset_token(
            user_id, digest, expires_at,
            ip_address=ip, user_agent=user_agent,
        )
    else:
        from app.database import set_rls_context

        await set_rls_context(db, str(user_id))
        db.add(
            PasswordResetToken(
                user_id=user_id,
                token_hash=digest,
                expires_at=expires_at,
                ip_address=ip,
                user_agent=user_agent,
            )
        )
        await db.commit()
    return raw


async def _revoke_all_refresh_tokens(
    db: Optional[AsyncSession], user_id: uuid.UUID
) -> None:
    if settings.demo_mode:
        demo_auth.revoke_all_refresh_tokens(user_id)
        return
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()


def _user_email_verified(user) -> bool:
    """Return True if the user does not require verification (e.g. wallet
    user) or has already verified their email."""
    if not getattr(user, "email", None):
        return True  # wallet-only users are not subject to email verification
    return getattr(user, "email_verified_at", None) is not None


def _revoke_request_access_token(request: Request) -> bool:
    """P3-A5: blocklist the access token attached to this request.

    The middleware in ``app/main.py`` parses the bearer JWT and stashes
    the payload on ``request.state.access_token_payload``. We use that
    to find the ``jti`` and the ``exp`` so the Redis blocklist entry
    expires together with the token. Returns the durability of the
    revoke (False = Redis unavailable; caller can decide to surface).
    """
    from app.core.token_blocklist import revoke

    payload = getattr(request.state, "access_token_payload", None)
    if not payload:
        return False
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti:
        return False
    return revoke(jti, exp=exp)


def _generic_token_invalid_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Token is invalid or expired",
    )


# ── /register ────────────────────────────────────────────────────────────────
@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit(settings.register_rate_limit, exempt_when=_exempt_register_in_demo)
async def register(
    request: Request,
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user (P3-A8 rate-limited).

    For email-bearing accounts a single-use email-verification token
    is issued. The raw token is included on the response in
    demo/dev mode so the test suite can drive the verification flow
    without an email service. In production the operator wires an
    email transport that consumes the issued token; the response
    body never carries the raw token there.
    """
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
        # Backwards-compatibility: when verification is NOT required,
        # mark the email verified at creation time so existing tests
        # and demo flows that immediately log in still work.
        if user_data.email and not settings.email_verification_required:
            demo_auth.mark_email_verified(new_user.id)
        if user_data.email:
            await _issue_email_verification_token(None, new_user.id)
        await record_auth_event(
            "register",
            target_user_id=new_user.id,
            request=request,
            payload={
                "has_email": bool(user_data.email),
                "has_wallet": bool(user_data.wallet_address),
            },
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
        email_verified_at=(
            None
            if (user_data.email and settings.email_verification_required)
            else (datetime.utcnow() if user_data.email else None)
        ),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if user.email:
        await _issue_email_verification_token(db, user.id)
    await record_auth_event(
        "register", target_user_id=user.id, request=request,
        payload={"has_email": bool(user.email), "has_wallet": bool(user.wallet_address)},
    )
    return user


# ── /verify-email ────────────────────────────────────────────────────────────
@router.post("/verify-email/confirm", status_code=status.HTTP_200_OK)
async def verify_email_confirm(
    body: EmailVerificationConfirm,
    db: AsyncSession = Depends(get_db),
):
    """Consume a verification token and mark the user's email verified.

    Idempotent: a token can only be consumed once. Subsequent attempts
    with the same token return 400. Returns ``{"verified": true}`` on
    success so the client can update its UI.
    """
    digest = hash_url_safe_token(body.token)

    if settings.demo_mode:
        user_id = demo_auth.consume_email_verification_token(digest)
        if user_id is None:
            raise _generic_token_invalid_exception()
        demo_auth.mark_email_verified(user_id)
        return {"verified": True}

    # The lookup is allowed without RLS context via the
    # ``email_verification_tokens_user_isolation_lookup`` SELECT-only
    # policy from migration 014.
    result = await db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == digest
        )
    )
    token = result.scalar_one_or_none()
    if (
        token is None
        or token.consumed_at is not None
        or token.expires_at < datetime.utcnow()
    ):
        raise _generic_token_invalid_exception()

    # Now we know the user_id; bind RLS so the UPDATE statements pass
    # WITH CHECK on both tables.
    from app.database import set_rls_context

    await set_rls_context(db, str(token.user_id))
    await db.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.id == token.id)
        .values(consumed_at=datetime.utcnow())
    )
    await db.execute(
        update(User)
        .where(User.id == token.user_id, User.email_verified_at.is_(None))
        .values(email_verified_at=datetime.utcnow())
    )
    await db.commit()
    return {"verified": True}


@router.post("/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
async def verify_email_resend(
    body: EmailVerificationResendRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resend a verification token. Always returns 202 to avoid
    leaking whether a given email is registered."""
    if settings.demo_mode:
        user = demo_auth.get_user_by_email(body.email)
        if user is not None and user.email_verified_at is None:
            await _issue_email_verification_token(None, user.id)
        return {"status": "ok"}

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is not None and user.email_verified_at is None:
        await _issue_email_verification_token(db, user.id)
    return {"status": "ok"}


# ── /login ───────────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.login_rate_limit, exempt_when=_exempt_register_in_demo)
async def login(
    request: Request,
    response: Response,
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
        if user is not None:
            await record_auth_event(
                "login_failure",
                target_user_id=user.id,
                request=request,
                payload={"reason": "inactive"},
            )
        # Note: when the email is unknown we deliberately do NOT
        # write an audit row — we have no target_user_id and the
        # brute-force tracker already records the email-shaped
        # signal needed to detect enumeration.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID)
    if not user.hashed_password:
        await record_auth_event(
            "login_failure",
            target_user_id=user.id,
            request=request,
            payload={"reason": "no_password"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account has no password. Use wallet login or API key.",
        )
    if not verify_password(credentials.password, user.hashed_password):
        await record_failure(request, credentials.email)
        await record_auth_event(
            "login_failure",
            target_user_id=user.id,
            request=request,
            payload={"reason": "wrong_password"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID)

    # P3-A1: gate on email_verified_at when the operator opts in.
    if settings.email_verification_required and not _user_email_verified(user):
        await record_auth_event(
            "login_failure",
            target_user_id=user.id,
            request=request,
            payload={"reason": "email_unverified"},
        )
        # Use a distinct status so the client can prompt for verification.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Check your inbox for the verification link.",
        )

    await clear_failures(credentials.email)

    # P3-A6 (Block 5): if the user has TOTP enabled, do NOT mint a
    # full token pair yet. Instead return a short-lived
    # ``totp_session_token`` the client must trade in via
    # /auth/totp/complete-login alongside a valid 6-digit code. The
    # access token surface is unchanged for users who have not
    # enrolled in TOTP, so this is fully backwards-compatible.
    if getattr(user, "totp_enabled", False):
        from app.core.security import create_totp_session_token

        totp_token = create_totp_session_token(str(user.id))
        await record_auth_event(
            "login_totp_required",
            target_user_id=user.id,
            request=request,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "requires_totp": True,
                "totp_session_token": totp_token,
                "expires_in": 300,
            },
        )

    access, refresh = _issue_token_pair(user.id)
    await _persist_refresh_token(db, user.id, refresh, request)
    await record_auth_event(
        "login_success", target_user_id=user.id, request=request
    )

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
        # Without RLS context set, the users_login_lookup policy lets
        # us read this row by id. After we know who the caller is, we
        # set the context so subsequent refresh_tokens reads/writes
        # pass RLS.
        user_q = await db.execute(select(User).where(User.id == user_uuid))
        user = user_q.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    if not settings.demo_mode:
        from app.database import set_rls_context as _set_rls

        await _set_rls(db, str(user_uuid))

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
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the current session's refresh token. Idempotent."""
    token_hash = hash_refresh_token(body.refresh_token)
    if settings.demo_mode:
        demo_auth.revoke_refresh_token(token_hash, current_user.id)
    else:
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
    await record_auth_event(
        "logout", target_user_id=current_user.id, request=request
    )


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke every refresh token for the current user."""
    await _revoke_all_refresh_tokens(db, current_user.id)
    await record_auth_event(
        "logout_all", target_user_id=current_user.id, request=request
    )


# ── /sessions (P3-A4) ────────────────────────────────────────────────────────
@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the user's currently active refresh tokens."""
    if settings.demo_mode:
        rows = demo_auth.list_active_refresh_tokens(current_user.id)
        return [
            SessionResponse(
                id=row["id"],
                issued_at=row["issued_at"],
                expires_at=row["expires_at"],
                user_agent=row.get("user_agent"),
                ip_address=row.get("ip_address"),
            )
            for row in rows
        ]
    result = await db.execute(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.utcnow(),
        )
        .order_by(RefreshToken.issued_at.desc())
    )
    return [
        SessionResponse(
            id=row.id,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
            user_agent=row.user_agent,
            ip_address=row.ip_address,
        )
        for row in result.scalars().all()
    ]


@router.delete(
    "/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_session(
    session_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a single session by row id (P3-A4)."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session id")

    if settings.demo_mode:
        if not demo_auth.revoke_refresh_token_by_id(sid, current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
            )
        await record_auth_event(
            "session_revoke",
            target_user_id=current_user.id,
            request=request,
            payload={"session_id": str(sid)},
        )
        return
    result = await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.id == sid,
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.utcnow())
        .returning(RefreshToken.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    await db.commit()
    await record_auth_event(
        "session_revoke",
        target_user_id=current_user.id,
        request=request,
        payload={"session_id": str(sid)},
    )


# ── /password-reset (P3-A2) ──────────────────────────────────────────────────
@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def password_reset_request(
    body: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Issue a single-use password reset token.

    Always returns 202 — the response is identical whether or not the
    address is registered, so the route does not leak account
    existence. In demo / dev mode the raw token is logged so the test
    suite can pick it up; in production the operator's email
    transport delivers it.
    """
    if settings.demo_mode:
        user = demo_auth.get_user_by_email(body.email)
    else:
        result = await db.execute(select(User).where(User.email == body.email))
        user = result.scalar_one_or_none()

    if user is not None and user.is_active:
        await _issue_password_reset_token(db, user.id, request)
        await record_auth_event(
            "password_reset_request",
            target_user_id=user.id,
            request=request,
        )
    return {"status": "ok"}


@router.post("/password-reset/confirm", status_code=status.HTTP_200_OK)
async def password_reset_confirm(
    body: PasswordResetConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Consume a reset token, rotate the password, revoke all sessions."""
    digest = hash_url_safe_token(body.token)

    if settings.demo_mode:
        user_id = demo_auth.consume_password_reset_token(digest)
        if user_id is None:
            raise _generic_token_invalid_exception()
        demo_auth.update_user_password(user_id, get_password_hash(body.new_password))
        demo_auth.revoke_all_refresh_tokens(user_id)
        await record_auth_event(
            "password_reset_confirm",
            target_user_id=user_id,
            request=request,
        )
        return {"status": "ok"}

    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == digest)
    )
    token = result.scalar_one_or_none()
    if (
        token is None
        or token.consumed_at is not None
        or token.expires_at < datetime.utcnow()
    ):
        raise _generic_token_invalid_exception()

    from app.database import set_rls_context

    await set_rls_context(db, str(token.user_id))
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.id == token.id)
        .values(consumed_at=datetime.utcnow())
    )
    await db.execute(
        update(User)
        .where(User.id == token.user_id)
        .values(hashed_password=get_password_hash(body.new_password))
    )
    await db.commit()
    await _revoke_all_refresh_tokens(db, token.user_id)
    await record_auth_event(
        "password_reset_confirm",
        target_user_id=token.user_id,
        request=request,
    )
    return {"status": "ok"}


# ── /change-password (P3-A3) ─────────────────────────────────────────────────
@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rotate the password from inside an authenticated session.

    Requires the *current* password (so a stolen access token alone
    cannot change the password). Revokes every refresh token for the
    user on success — the caller must log in again on every device.
    P3-A5: also adds the current request's access-token ``jti`` to
    the Redis blocklist so a stolen access token cannot keep
    operating until its 4-hour expiry.
    """
    if not current_user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account has no password to change.",
        )
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )
    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password",
        )

    new_hash = get_password_hash(body.new_password)
    if settings.demo_mode:
        demo_auth.update_user_password(current_user.id, new_hash)
        demo_auth.revoke_all_refresh_tokens(current_user.id)
    else:
        await db.execute(
            update(User)
            .where(User.id == current_user.id)
            .values(hashed_password=new_hash)
        )
        await db.commit()
        await _revoke_all_refresh_tokens(db, current_user.id)
    _revoke_request_access_token(request)
    await record_auth_event(
        "password_change", target_user_id=current_user.id, request=request
    )
    return {"status": "ok"}


@router.post("/revoke-current-token", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_current_token(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """P3-A5: blocklist this exact access token immediately.

    Useful when the user reports a stolen device or wants to log out
    of "this browser only" without revoking every refresh token.
    The TTL of the blocklist entry equals the token's remaining
    lifetime so the entry expires together with the token.
    """
    if not _revoke_request_access_token(request):
        # Redis unavailable. The blocklist is the only path to
        # immediate revocation, so we surface a clear 503 rather
        # than pretending success.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Token revocation is temporarily unavailable. "
                "Use /auth/logout-all to revoke every refresh token "
                "and let the access token expire naturally (max "
                f"{settings.access_token_expire_hours}h)."
            ),
        )


# ── /api-keys ────────────────────────────────────────────────────────────────
@router.post(
    "/api-keys",
    response_model=APIKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    api_key_data: APIKeyCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raw_key, key_hash = generate_api_key()
    if settings.demo_mode:
        record = demo_auth.add_api_key(current_user.id, key_hash, api_key_data.name)
        await record_auth_event(
            "api_key_create",
            target_user_id=current_user.id,
            request=request,
            payload={"api_key_id": str(record.id), "name": record.name},
        )
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
    await record_auth_event(
        "api_key_create",
        target_user_id=current_user.id,
        request=request,
        payload={"api_key_id": str(api_key.id), "name": api_key.name},
    )
    return APIKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        api_key=raw_key,
        scopes=api_key.scopes,
        created_at=api_key.created_at,
    )


# P3-A10: atomic API key rotation. The old key continues to work
# until the operator deletes it; this endpoint returns the new raw
# key plus the metadata of both. Callers who want a hard
# rotation should follow up with DELETE /auth/api-keys/{old_id}
# after they have updated their consumers.
@router.post(
    "/api-keys/{key_id}/rotate",
    response_model=APIKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_api_key(
    key_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Atomically issue a new API key with the same name + scopes.

    Atomicity: the new key is inserted before any other action.
    If the insert fails the old key is still alive — the worst-case
    failure mode is "rotate route returned 5xx; nothing changed".

    The response carries the *new* raw key. The old key's hash is
    not returned. Audit log records both key ids so an operator
    can reconstruct the rotation chain.
    """
    try:
        old_uuid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid key id")

    raw_key, key_hash = generate_api_key()

    if settings.demo_mode:
        old = demo_auth.get_api_key_by_id(old_uuid, current_user.id)
        if old is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
            )
        new_record = demo_auth.add_api_key(
            current_user.id, key_hash, old.name
        )
        await record_auth_event(
            "api_key_rotate",
            target_user_id=current_user.id,
            request=request,
            payload={
                "old_api_key_id": str(old_uuid),
                "new_api_key_id": str(new_record.id),
                "name": new_record.name,
            },
        )
        return APIKeyCreateResponse(
            id=new_record.id,
            name=new_record.name,
            api_key=raw_key,
            scopes=new_record.scopes,
            created_at=new_record.created_at,
        )

    result = await db.execute(
        select(APIKey).where(
            APIKey.id == old_uuid, APIKey.user_id == current_user.id
        )
    )
    old = result.scalar_one_or_none()
    if old is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )

    new_key = APIKey(
        user_id=current_user.id,
        key_hash=key_hash,
        name=old.name,
        scopes=old.scopes,
        is_active=True,
    )
    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)
    await record_auth_event(
        "api_key_rotate",
        target_user_id=current_user.id,
        request=request,
        payload={
            "old_api_key_id": str(old_uuid),
            "new_api_key_id": str(new_key.id),
            "name": new_key.name,
        },
    )
    return APIKeyCreateResponse(
        id=new_key.id,
        name=new_key.name,
        api_key=raw_key,
        scopes=new_key.scopes,
        created_at=new_key.created_at,
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
    request: Request,
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
        await record_auth_event(
            "api_key_delete",
            target_user_id=current_user.id,
            request=request,
            payload={"api_key_id": str(key_uuid)},
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
    await record_auth_event(
        "api_key_delete",
        target_user_id=current_user.id,
        request=request,
        payload={"api_key_id": str(key_uuid)},
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=current_user.id,
        email=getattr(current_user, "email", None),
        wallet_address=getattr(current_user, "wallet_address", None),
        is_active=current_user.is_active,
        created_at=current_user.created_at,
    )
