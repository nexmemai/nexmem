"""App ORM model — Phase 4 (P4-B1).

Apps are first-class entities owned by a user. An app is the unit of
isolation for app-level RLS (P4-B4), per-app quotas (P4-B5, future),
and app suspension (P4-B6, future).

Backwards-compatibility note
----------------------------
- Existing memory-table rows have ``app_id`` already (migrations 005,
  006). Those values are NOT touched by Phase 4; they continue to point
  to whatever opaque UUID the application supplied.
- Existing ``api_keys`` rows acquire a nullable ``app_id`` FK via
  migration 018 (P4-B2); rows are backfilled to NULL. Tying an existing
  API key to an ``apps`` row is an explicit operator action.
- The app-level RLS policy in migration 019 (P4-B4) accepts both the
  legacy "no current app" mode (``app_id IS NULL`` rows) and the new
  app-scoped mode (``app_id = current_setting('app.current_app_id')``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class App(Base):
    """A user-owned application. The unit of multi-tenant isolation."""

    __tablename__ = "apps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    # P4-B6 (Block 7): operator-controlled suspension. Both columns
    # default NULL so this is a pure additive change for existing
    # rows — see migration 024_app_suspension. ``is_active`` is left
    # alone; suspension is independent of the user-facing
    # active/inactive flag (a user-deactivated app has is_active=False
    # without ever being suspended; an operator-suspended app has
    # suspended_at != NULL while is_active stays True).
    suspended_at = Column(DateTime(timezone=True), nullable=True)
    suspension_reason = Column(Text, nullable=True)

    @property
    def is_suspended(self) -> bool:
        """True iff the app has been operator-suspended.

        Implemented as a Python-side property (not a ``hybrid_property``)
        because the only call sites are the suspension-check
        dependency and the audit-log payload — neither of which needs
        the predicate inside a SQL filter today. Promoting to a
        ``hybrid_property`` is a one-line change if a query like
        ``select(App).where(App.is_suspended)`` ever shows up.
        """
        return self.suspended_at is not None

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<App id={self.id} user_id={self.user_id} name={self.name!r}>"
