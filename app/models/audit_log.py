"""ORM models for the gdpr + auth audit logs (P10-H1, P10-H2)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class _AuditLogMixin:
    """Shared columns for audit-log tables.

    Subclasses set ``__tablename__`` and add their own indexes via
    ``__table_args__``.
    """

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # FK -> users.id with ON DELETE SET NULL on actor (we keep the
    # audit row when the actor account is deleted) and CASCADE on
    # target (GDPR delete sweeps these rows along with everything
    # else for that user).
    actor_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    target_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    action = Column(String(64), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    request_id = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


class GDPRAuditLog(_AuditLogMixin, Base):
    """One row per GDPR action (export / delete / consent change)."""

    __tablename__ = "gdpr_audit_log"
    __table_args__ = (
        Index(
            "ix_gdpr_audit_log_target_user_id_created_at",
            "target_user_id",
            "created_at",
        ),
        Index("ix_gdpr_audit_log_request_id", "request_id"),
    )


class AuthAuditLog(_AuditLogMixin, Base):
    """One row per security-relevant auth action.

    Actions (free-form strings — no enum, see migration docstring):

    * ``register``                — successful new user creation
    * ``login_success`` / ``login_failure``
    * ``logout`` / ``logout_all``
    * ``password_change``         — authenticated change-password
    * ``password_reset_request``  — even if email unknown (so we can
                                    detect enumeration attempts)
    * ``password_reset_confirm``
    * ``email_verify_confirm``
    * ``access_token_revoke``
    * ``api_key_create`` / ``api_key_delete`` / ``api_key_rotate``
    * ``session_revoke``
    """

    __tablename__ = "auth_audit_log"
    __table_args__ = (
        Index(
            "ix_auth_audit_log_target_user_id_created_at",
            "target_user_id",
            "created_at",
        ),
        Index("ix_auth_audit_log_request_id", "request_id"),
    )
