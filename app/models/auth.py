"""Auth-related ORM models added in Phase 2.

Refresh tokens are stored hashed (SHA-256) so that a leaked database
backup cannot be used to mint new access tokens. Revocation is real:
``logout`` deletes the row, ``logout-all`` deletes every row for the
user. Lookup is constant-time via ``secrets.compare_digest``.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Index
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class RefreshToken(Base):
    """A revocable refresh token issued to a user.

    The ``token_hash`` column is sha256(raw_token).hexdigest(). The raw
    token is only ever returned to the client at issue time.
    """

    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(String, unique=True, nullable=False, index=True)
    issued_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    # Track which API key issued the token (when login was via API key) so
    # operators can invalidate every session created by a compromised key.
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_refresh_tokens_user_id_revoked_at", "user_id", "revoked_at"),
    )
