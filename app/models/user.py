"""User and API Key ORM models — Day 2."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class User(Base):
    """Represents a registered user (email/password or wallet)."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=True, index=True)
    wallet_address = Column(String, unique=True, nullable=True, index=True)
    hashed_password = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    tier = Column(String, default="free", nullable=False)  # free, starter, pro, enterprise
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    total_tokens_used = Column(Integer, default=0, nullable=False)
    # Phase 3 (P3-A1): set when the user confirms their email. Login is
    # gated on this column when EMAIL_VERIFICATION_REQUIRED=true.
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    # Phase 3 (P3-A6, Block 5): TOTP / 2FA.
    # ``totp_secret`` is the base32 RFC 6238 shared secret (32 chars,
    # never returned to the client after the initial setup response).
    # ``totp_enabled`` is flipped to True only after the user proves
    # they can produce a valid code, so a half-completed setup never
    # locks the user out.
    totp_secret = Column(String(32), nullable=True, default=None)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    # Phase 7 (P7-E4, Block 5): GDPR soft-delete grace period.
    # ``deletion_requested_at`` is stamped when the user calls
    # DELETE /memory/user/{id}/all. ``deletion_scheduled_for`` is
    # ``deletion_requested_at + DELETION_GRACE_DAYS`` and is what the
    # ``execute_scheduled_deletions`` Celery task scans for. The user
    # is set ``is_active=False`` at request time so authenticated
    # routes immediately return 401, but the row + every memory
    # row stays intact until the grace period elapses, giving the
    # user a window to cancel.
    deletion_requested_at = Column(DateTime(timezone=True), nullable=True, default=None)
    deletion_scheduled_for = Column(DateTime(timezone=True), nullable=True, default=None)


class APIKey(Base):
    """Named API key scoped to a user. Raw key is shown exactly once."""

    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key_hash = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)          # e.g. "Telegram Bot", "VS Code"
    scopes = Column(String, default="read,write")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    # Phase 4 (P4-B2): optional binding to an Apps row. NULL means
    # "legacy / unbound API key, not yet migrated to first-class apps".
    # ON DELETE SET NULL: deleting the App must not cascade to deleting
    # the API key — the operator may want to re-bind the key later.
    app_id = Column(
        UUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class TokenUsage(Base):
    """Track token usage per user/app for billing/cost tracking."""

    __tablename__ = "token_usage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    app_id = Column(String, nullable=True, index=True)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    model = Column(String, nullable=False)
    cost_cents = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
