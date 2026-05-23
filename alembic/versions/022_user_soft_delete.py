"""Soft-delete grace period on users (P7-E4, Block 5).

Revision ID: 022_user_soft_delete
Revises: 021_totp_fields
Create Date: 2026-05-23

Adds two timestamp columns to ``users`` so the GDPR delete flow can
move from immediate cascade-delete to a 30-day grace period:

* ``deletion_requested_at TIMESTAMPTZ NULL`` — set at the moment the
  user calls ``DELETE /memory/user/{id}/all``. Read-only audit
  signal; the route never updates this twice for the same window.

* ``deletion_scheduled_for TIMESTAMPTZ NULL`` — set to
  ``deletion_requested_at + 30 days``. The
  ``execute_scheduled_deletions`` Celery task scans for rows where
  this column is non-NULL and ``<= now()`` and executes the actual
  cascade.

Both columns default NULL so existing users — who have never
requested deletion — see no change at all. No backfill is required.

Index posture: no index added. Reads of these columns are scoped to
either (a) the authenticated user's own row (PK lookup) or (b) the
nightly Celery scan, which is small enough that a sequential scan is
fine even at six-figure user counts. If the cohort grows past that
the operator can add ``CREATE INDEX CONCURRENTLY`` later in a
follow-up migration.

The previous immediate-delete behaviour required no schema; this
migration only relaxes it. The matching application-level changes
live in ``app/routers/gdpr.py`` (route refactor + new
``/cancel-deletion`` route) and ``app/tasks.py``
(``execute_scheduled_deletions`` Celery task).

Downgrade drops both columns; nothing else references them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "022_user_soft_delete"
down_revision: Union[str, None] = "021_totp_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "deletion_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "deletion_scheduled_for",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "deletion_scheduled_for")
    op.drop_column("users", "deletion_requested_at")
