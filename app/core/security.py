import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Union, Tuple
from jose import jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"


def create_access_token(
    subject: Union[str, Any], expires_delta: timedelta = None
) -> str:
    """Create a standard JWT access token."""
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            hours=settings.access_token_expire_hours
        )
    to_encode = {"exp": expire, "sub": str(subject), "type": "access"}
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(
    subject: Union[str, Any], expires_delta: timedelta = None
) -> str:
    """Create a JWT refresh token."""
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        # Default refresh token lifetime: 7 days
        expire = datetime.utcnow() + timedelta(days=7)
    to_encode = {"exp": expire, "sub": str(subject), "type": "refresh"}
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)
    return encoded_jwt


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def generate_api_key() -> Tuple[str, str]:
    """
    Generate a new API key and its hash.
    Returns: (raw_key, hashed_key)
    """
    # 32 bytes of randomness gives 256 bits of entropy
    raw_key = "mem_" + secrets.token_urlsafe(32)
    # Using SHA-256 for API keys (faster than bcrypt, perfectly fine for high-entropy keys)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


def verify_api_key(plain_key: str, hashed_key: str) -> bool:
    """Verify a raw API key against a stored hash."""
    expected_hash = hashlib.sha256(plain_key.encode()).hexdigest()
    # Use secrets.compare_digest to prevent timing attacks
    return secrets.compare_digest(expected_hash, hashed_key)
