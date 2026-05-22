"""In-memory user/api-key/refresh-token store for DEMO_MODE.

Production runs against the real ORM tables. DEMO_MODE is a zero-
dependency mode used by the test suite (no Postgres, no Redis). This
module gives demo mode a coherent multi-user auth flow so tests can
exercise register / login / refresh / logout / api-key flows without
a database.

The shapes intentionally mirror the production ORM models so router
code only needs a thin ``if settings.demo_mode`` branch instead of
two parallel implementations.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app import demo_db


@dataclass
class DemoUser:
    id: uuid.UUID
    email: Optional[str] = None
    wallet_address: Optional[str] = None
    hashed_password: Optional[str] = None
    is_active: bool = True
    tier: str = "free"
    created_at: datetime = field(default_factory=datetime.utcnow)
    total_tokens_used: int = 0
    email_verified_at: Optional[datetime] = None


@dataclass
class DemoAPIKey:
    id: uuid.UUID
    user_id: uuid.UUID
    key_hash: str
    name: str
    scopes: str = "read,write"
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None


def _coerce_user(record: dict) -> DemoUser:
    return DemoUser(**record)


def get_user_by_email(email: str) -> Optional[DemoUser]:
    user_id = demo_db.demo_users_by_email.get(email.lower())
    if not user_id:
        return None
    record = demo_db.demo_users.get(user_id)
    return _coerce_user(record) if record else None


def get_user_by_id(user_id: str) -> Optional[DemoUser]:
    record = demo_db.demo_users.get(str(user_id))
    return _coerce_user(record) if record else None


def get_user_by_wallet(wallet: str) -> Optional[DemoUser]:
    for record in demo_db.demo_users.values():
        if record.get("wallet_address") == wallet:
            return _coerce_user(record)
    return None


def create_user(
    email: Optional[str],
    wallet_address: Optional[str],
    hashed_password: Optional[str],
) -> DemoUser:
    new_id = uuid.uuid4()
    record = {
        "id": new_id,
        "email": email,
        "wallet_address": wallet_address,
        "hashed_password": hashed_password,
        "is_active": True,
        "tier": "free",
        "created_at": datetime.utcnow(),
        "total_tokens_used": 0,
        "email_verified_at": None,
    }
    demo_db.demo_users[str(new_id)] = record
    if email:
        demo_db.demo_users_by_email[email.lower()] = str(new_id)
    return _coerce_user(record)


def update_user_password(user_id: uuid.UUID, hashed_password: str) -> None:
    """Rotate a demo user's hashed password (P3-A2, P3-A3)."""
    rec = demo_db.demo_users.get(str(user_id))
    if rec is not None:
        rec["hashed_password"] = hashed_password


def mark_email_verified(user_id: uuid.UUID, when: Optional[datetime] = None) -> None:
    """Set email_verified_at on a demo user (P3-A1)."""
    rec = demo_db.demo_users.get(str(user_id))
    if rec is not None:
        rec["email_verified_at"] = when or datetime.utcnow()


# ── API keys ─────────────────────────────────────────────────────────────────
def add_api_key(user_id: uuid.UUID, key_hash: str, name: str) -> DemoAPIKey:
    new_id = uuid.uuid4()
    key = DemoAPIKey(id=new_id, user_id=user_id, key_hash=key_hash, name=name)
    demo_db.demo_api_keys[str(new_id)] = {
        "id": key.id,
        "user_id": key.user_id,
        "key_hash": key.key_hash,
        "name": key.name,
        "scopes": key.scopes,
        "is_active": key.is_active,
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
    }
    return key


def list_api_keys_for_user(user_id: uuid.UUID) -> list[DemoAPIKey]:
    out: list[DemoAPIKey] = []
    for rec in demo_db.demo_api_keys.values():
        if str(rec["user_id"]) == str(user_id):
            out.append(DemoAPIKey(**rec))
    return sorted(out, key=lambda k: k.created_at, reverse=True)


def get_api_key_by_id(key_id: uuid.UUID, user_id: uuid.UUID) -> Optional[DemoAPIKey]:
    rec = demo_db.demo_api_keys.get(str(key_id))
    if rec is None or str(rec["user_id"]) != str(user_id):
        return None
    return DemoAPIKey(**rec)


def get_api_key_by_hash(key_hash: str) -> Optional[DemoAPIKey]:
    for rec in demo_db.demo_api_keys.values():
        if rec["key_hash"] == key_hash and rec["is_active"]:
            return DemoAPIKey(**rec)
    return None


def delete_api_key(key_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    rec = demo_db.demo_api_keys.get(str(key_id))
    if rec is None or str(rec["user_id"]) != str(user_id):
        return False
    del demo_db.demo_api_keys[str(key_id)]
    return True


# ── Refresh tokens ───────────────────────────────────────────────────────────
def add_refresh_token(
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> uuid.UUID:
    """Insert a refresh-token row and return its synthetic id.

    The id mirrors ``RefreshToken.id`` in production so the
    ``/auth/sessions`` listing endpoint can return a stable handle the
    client uses to revoke a single session.
    """
    new_id = uuid.uuid4()
    demo_db.demo_refresh_tokens[token_hash] = {
        "id": new_id,
        "user_id": user_id,
        "token_hash": token_hash,
        "issued_at": datetime.utcnow(),
        "expires_at": expires_at,
        "revoked_at": None,
        "user_agent": user_agent,
        "ip_address": ip_address,
    }
    return new_id


def is_refresh_token_active(token_hash: str, user_id: uuid.UUID) -> bool:
    rec = demo_db.demo_refresh_tokens.get(token_hash)
    if not rec or str(rec["user_id"]) != str(user_id):
        return False
    if rec["revoked_at"] is not None:
        return False
    if rec["expires_at"] < datetime.utcnow():
        return False
    return True


def revoke_refresh_token(token_hash: str, user_id: uuid.UUID) -> None:
    rec = demo_db.demo_refresh_tokens.get(token_hash)
    if rec and str(rec["user_id"]) == str(user_id):
        rec["revoked_at"] = datetime.utcnow()


def revoke_all_refresh_tokens(user_id: uuid.UUID) -> None:
    for rec in demo_db.demo_refresh_tokens.values():
        if str(rec["user_id"]) == str(user_id) and rec["revoked_at"] is None:
            rec["revoked_at"] = datetime.utcnow()


def list_active_refresh_tokens(user_id: uuid.UUID) -> list[dict]:
    """Return the live (non-revoked, non-expired) refresh-token records.

    Used by ``GET /auth/sessions`` (P3-A4). Returns dicts so the
    caller can map them to the ``SessionResponse`` schema.
    """
    now = datetime.utcnow()
    out: list[dict] = []
    for rec in demo_db.demo_refresh_tokens.values():
        if str(rec["user_id"]) != str(user_id):
            continue
        if rec["revoked_at"] is not None:
            continue
        if rec["expires_at"] < now:
            continue
        out.append(rec)
    return sorted(out, key=lambda r: r["issued_at"], reverse=True)


def revoke_refresh_token_by_id(session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Revoke a single session by row id (P3-A4)."""
    for rec in demo_db.demo_refresh_tokens.values():
        if (
            rec.get("id") == session_id
            and str(rec["user_id"]) == str(user_id)
            and rec["revoked_at"] is None
        ):
            rec["revoked_at"] = datetime.utcnow()
            return True
    return False


# ── Email verification tokens (P3-A1) ────────────────────────────────────────
def add_email_verification_token(
    user_id: uuid.UUID, token_hash: str, expires_at: datetime
) -> None:
    demo_db.demo_email_verification_tokens[token_hash] = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "token_hash": token_hash,
        "created_at": datetime.utcnow(),
        "expires_at": expires_at,
        "consumed_at": None,
    }


def consume_email_verification_token(token_hash: str) -> Optional[uuid.UUID]:
    """Atomically consume a verification token. Returns user_id on success."""
    rec = demo_db.demo_email_verification_tokens.get(token_hash)
    if rec is None:
        return None
    if rec["consumed_at"] is not None:
        return None
    if rec["expires_at"] < datetime.utcnow():
        return None
    rec["consumed_at"] = datetime.utcnow()
    return rec["user_id"]


# ── Password reset tokens (P3-A2) ────────────────────────────────────────────
def add_password_reset_token(
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    demo_db.demo_password_reset_tokens[token_hash] = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "token_hash": token_hash,
        "created_at": datetime.utcnow(),
        "expires_at": expires_at,
        "consumed_at": None,
        "ip_address": ip_address,
        "user_agent": user_agent,
    }


def consume_password_reset_token(token_hash: str) -> Optional[uuid.UUID]:
    """Atomically consume a reset token. Returns user_id on success."""
    rec = demo_db.demo_password_reset_tokens.get(token_hash)
    if rec is None:
        return None
    if rec["consumed_at"] is not None:
        return None
    if rec["expires_at"] < datetime.utcnow():
        return None
    rec["consumed_at"] = datetime.utcnow()
    return rec["user_id"]
