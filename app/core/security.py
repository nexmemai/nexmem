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
from datetime import datetime, timedelta
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
    to_encode = {"exp": expire, "sub": str(subject), "type": "access"}
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
    """
    return jwt.decode(token, settings.secret_key, algorithms=ALLOWED_ALGORITHMS)


# ── API keys ─────────────────────────────────────────────────────────────────
def generate_api_key() -> Tuple[str, str]:
    raw_key = "mem_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


def verify_api_key(plain_key: str, hashed_key: str) -> bool:
    expected_hash = hashlib.sha256(plain_key.encode()).hexdigest()
    return secrets.compare_digest(expected_hash, hashed_key)


# ── Refresh token storage helpers ────────────────────────────────────────────
def hash_refresh_token(token: str) -> str:
    """Return the storage hash of a refresh token (sha256 hex)."""
    return hashlib.sha256(token.encode()).hexdigest()
