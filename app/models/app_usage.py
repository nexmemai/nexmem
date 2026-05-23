"""AppUsage ORM model — Phase 4 (P4-B5, Block 7).

One row per (app_id, month_year). The ``app/services/app_quota.py``
upsert is the only writer; readers are the dashboard endpoint
(``GET /apps/{app_id}/usage``) and unit tests.

Modelled with ``Column()`` (not ``Mapped[]``) to match the rest of
the codebase. The migration is the source of truth for the schema;
this model reflects it for ORM convenience and is NOT used by the
upsert path (which uses raw text() for atomic ON CONFLICT).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class AppUsage(Base):
    """Monthly write / read counters per app."""

    __tablename__ = "app_usage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id = Column(
        UUID(as_uuid=True),
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ``month_year`` is a fixed-width 7-char string ("YYYY-MM"). The
    # UNIQUE (app_id, month_year) constraint lives in the migration —
    # SQLAlchemy's UniqueConstraint repetition here would not change
    # the schema, only the metadata view. Migration is the source of
    # truth.
    month_year = Column(String(7), nullable=False)
    write_count = Column(Integer, nullable=False, default=0)
    read_count = Column(Integer, nullable=False, default=0)
    last_updated = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"<AppUsage app_id={self.app_id} month={self.month_year} "
            f"writes={self.write_count} reads={self.read_count}>"
        )
