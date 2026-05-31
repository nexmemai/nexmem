"""Crypto + token helpers.

Phase 2 changes:
* Added ``hash_refresh_token`` so refresh tokens can be persisted as
  digests (never plaintext) and looked up in constant time.
* Added ``decode_token`` which centralizes JWT decoding and is the
  only place that accepts a token. Algorithm is whitelisted to
  ``HS256`` and ``options={"verify_aud": False}`` is explicit so we
  cannot fall through to ``alg=none``.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Tuple, Union

from jose import jwt
from passlib.context import CryptContext

from app.config import settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Whitelisted JWT algorithms. Never accept ``none``.
ALGORITHM = "HS256"
ALLOWED_ALGORITHMS = ["HS256"]


# ── Password hashing ─────────────────────────────────────────────────────────
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ── JWT issuance ─────────────────────────────────────────────────────────────
def create_access_token(
    subject: Union[str, Any], expires_delta: timedelta = None
) -> str:
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            hours=settings.access_token_expire_hours
        )
    # P3-A5: every access token now carries a unique ``jti`` so it
    # can be added to the per-user blocklist without revoking every
    # token the user holds. The jti is a 128-bit random hex string.
    # P11-I3 (Block 6): also embed ``iat`` (issued-at, unix ts) so an
    # admin force-logout can revoke ALL tokens issued before a cutoff
    # without touching the per-token blocklist. Tokens issued after the
    # cutoff (e.g. a legitimate re-login) authenticate normally.
    to_encode = {
        "exp": expire,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "sub": str(subject),
        "type": "access",
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(
    subject: Union[str, Any],
    expires_delta: timedelta = None,
    jti: str | None = None,
) -> str:
    """Create a refresh token.

    A unique ``jti`` is embedded so the same token cannot be re-minted
    after revocation. The caller (``app.routers.auth.login``) is
    responsible for persisting ``hash_refresh_token(jwt)`` so that the
    token is revocable.
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            days=settings.refresh_token_expire_days
        )
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh",
        "jti": jti or secrets.token_hex(16),
    }
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT.

    Whitelists ``HS256`` so a token signed with ``alg=none`` cannot be
    accepted. Raises ``jose.JWTError`` on any failure (signature,
    expiry, malformed). The caller turns that into HTTP 401.

    P3-A5: after signature + expiry validation succeed, the ``jti``
    claim is checked against the access-token blocklist. A revoked
    access token raises ``JWTError`` so the rest of the auth path
    treats it identically to any other invalid token.
    """
    payload = jwt.decode(
        token, settings.secret_key, algorithms=ALLOWED_ALGORITHMS
    )
    # The blocklist is only meaningful for access tokens. Refresh
    # tokens are revoked via the database row in ``refresh_tokens``;
    # checking them again here would be redundant.
    if payload.get("type") == "access":
        from app.core.token_blocklist import (
            get_user_revocation_cutoff,
            is_revoked,
        )

        if is_revoked(payload.get("jti")):
            from jose.exceptions import JWTError

            raise JWTError("access token revoked")
        # P11-I3 (Block 6): user-level force-logout cutoff. An admin
        # who hits POST /admin/users/{id}/force-logout sets a Redis
        # cutoff at ``now()``; every access token whose ``iat`` claim
        # is strictly older than that cutoff is rejected here. Fail
        # OPEN if Redis is unavailable (R-301 posture) — the cutoff
        # check returns None and the token passes the rest of
        # ``decode_token`` like usual. Demo-mode equivalent lives in
        # ``app.core.deps`` (against ``demo_db.demo_force_logout``)
        # because security.py intentionally has no demo coupling.
        sub = payload.get("sub")
        iat = payload.get("iat")
        if sub and iat is not None:
            cutoff = get_user_revocation_cutoff(str(sub))
            if cutoff is not None and int(iat) < int(cutoff):
                from jose.exceptions import JWTError

                raise JWTError("user access tokens force-logged-out")
    return payload


# ── API keys ─────────────────────────────────────────────────────────────────
def generate_api_key() -> Tuple[str, str]:
    raw_key = "nxm_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


def verify_api_key(plain_key: str, hashed_key: str) -> bool:
    expected_hash = hashlib.sha256(plain_key.encode()).hexdigest()
    return secrets.compare_digest(expected_hash, hashed_key)


# ── Refresh token storage helpers ────────────────────────────────────────────
def hash_refresh_token(token: str) -> str:
    """Return the storage hash of a refresh token (sha256 hex)."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Phase 3: email-verification + password-reset tokens ──────────────────────
# Both flows use a single shared shape:
#   * raw token = url-safe high-entropy string returned to the user once
#   * stored hash = sha256(raw) compared with secrets.compare_digest
# Reuse ``hash_refresh_token`` semantics: same hash function, different
# table. We expose dedicated helpers so callers do not import the
# refresh-token name in unrelated flows.
def generate_url_safe_token(nbytes: int = 32) -> tuple[str, str]:
    """Return ``(raw_token, sha256_hex)`` for a single-use email token.

    The raw token is URL-safe (suitable for embedding in a verification
    or reset link) with at least 256 bits of entropy. The hash is what
    the database stores; the raw token is only sent once.
    """
    raw = secrets.token_urlsafe(nbytes)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


def hash_url_safe_token(raw_token: str) -> str:
    """Return the storage hash for a single-use email token."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


# ── Phase 3 (P3-A6, Block 5): TOTP / 2FA helpers ─────────────────────────────
# All TOTP helpers import ``pyotp`` lazily so a sandbox without the
# dep can still ``import app.core.security`` (legacy posture from the
# Phase-2 era when pyotp was not yet a hard dep). pyotp is now in
# ``requirements.txt`` so production always has it.
def generate_totp_secret() -> str:
    """Return a fresh base32 RFC 6238 TOTP secret.

    Output length matches ``pyotp.random_base32()`` default (32 chars),
    which fits the ``users.totp_secret VARCHAR(32)`` column.
    """
    import pyotp  # noqa: WPS433

    return pyotp.random_base32()


def verify_totp_code(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code against ``secret``.

    ``valid_window=1`` accepts the previous, current, and next 30s
    windows so a user with a slightly skewed clock is not locked out.
    Returns False on any malformed input rather than raising — the
    caller turns False into an HTTP 400 / 401.
    """
    if not secret or not code:
        return False
    import pyotp  # noqa: WPS433

    try:
        return bool(pyotp.TOTP(secret).verify(code, valid_window=1))
    except Exception:
        # pyotp raises for non-base32 secrets or non-digit codes; we
        # treat that as a verification failure, never as a 500.
        return False


def generate_totp_uri(secret: str, email: str) -> str:
    """Return the ``otpauth://`` provisioning URI for a QR code.

    ``issuer_name="Nexmem"`` is the label the authenticator app shows
    next to the entry. The URI is rendered to PNG by the caller (see
    ``/auth/totp/setup``).
    """
    import pyotp  # noqa: WPS433

    return pyotp.TOTP(secret).provisioning_uri(
        name=email or "user", issuer_name="Nexmem"
    )


# Distinct ``type`` claim so a TOTP session token cannot be substituted
# for an access or refresh token by any code path that looks at
# ``payload["type"]``. ``decode_token`` only consults the access-token
# blocklist when ``type == "access"``, so this token is also outside
# the blocklist surface (which is what we want — it's already short-
# lived and single-use by design).
TOTP_PENDING_TOKEN_TYPE = "totp_pending"
TOTP_PENDING_TOKEN_TTL_MINUTES = 5


def create_totp_session_token(user_id: str) -> str:
    """Mint a short-lived JWT that proves the bearer has just supplied
    correct email + password and now owes a TOTP code.

    Lifetime is hardcoded at 5 minutes — long enough for a user to
    open their authenticator app and type a code, short enough that
    a leaked token is not useful for long. The token carries no
    ``access`` scope and is rejected by every protected route.
    """
    expire = datetime.utcnow() + timedelta(minutes=TOTP_PENDING_TOKEN_TTL_MINUTES)
    payload = {
        "exp": expire,
        "sub": str(user_id),
        "type": TOTP_PENDING_TOKEN_TYPE,
        "scope": TOTP_PENDING_TOKEN_TYPE,
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_totp_session_token(token: str) -> dict:
    """Decode a TOTP session token and verify its scope.

    Raises ``jose.JWTError`` (matching ``decode_token``'s contract)
    on signature failure, expiry, or wrong scope. The /auth/totp/
    complete-login route turns that into HTTP 401.
    """
    payload = jwt.decode(
        token, settings.secret_key, algorithms=ALLOWED_ALGORITHMS
    )
    if payload.get("type") != TOTP_PENDING_TOKEN_TYPE:
        from jose.exceptions import JWTError

        raise JWTError("not a totp_pending token")
    if payload.get("scope") != TOTP_PENDING_TOKEN_TYPE:
        from jose.exceptions import JWTError

        raise JWTError("totp_pending scope missing")
    return payload



# ── P11-I2 (Block 6): admin support impersonation ────────────────────────────
# Distinct ``type`` claim so an impersonation token cannot be substituted
# anywhere a normal access token is expected. ``decode_token`` only
# applies the per-user force-logout cutoff to ``type == "access"``,
# which intentionally lets admin-issued impersonation tokens survive a
# force-logout — the admin can still investigate the account afterwards.
# The audit log is the source of truth for who did what during an
# impersonation session; see ``app.routers.admin.impersonate_user``.
IMPERSONATION_TOKEN_TYPE = "impersonation"
IMPERSONATION_TOKEN_TTL_SECONDS = 3600


def create_impersonation_token(target_user_id: str) -> str:
    """Mint a short-lived JWT that lets the bearer act as ``target_user_id``.

    The token carries ``type=impersonation`` and an explicit
    ``actor="admin"`` claim so any code path that inspects the payload
    can tell impersonation from a normal user login at a glance.
    Lifetime is 1 h, hardcoded — long enough for a debugging session,
    short enough that a leaked token is not interesting tomorrow.
    """
    now = datetime.utcnow()
    expire = now + timedelta(seconds=IMPERSONATION_TOKEN_TTL_SECONDS)
    payload = {
        "sub": str(target_user_id),
        "type": IMPERSONATION_TOKEN_TYPE,
        "actor": "admin",
        "exp": expire,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
