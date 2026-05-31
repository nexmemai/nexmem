"""App suspension columns (P4-B6, Block 7).

Revision ID: 024_app_suspension
Revises: 023_app_usage_tracking
Create Date: 2026-05-23

Phase 4 — operator can suspend a misbehaving app without deleting it.

Adds two columns to ``apps``:

* ``suspended_at TIMESTAMPTZ NULL`` — set by the admin suspend route
  (``POST /api/v1/admin/apps/{app_id}/suspend``) at the moment of
  suspension. NULL means "not suspended"; that is the default for
  every existing row, so this migration changes no existing
  behaviour.

* ``suspension_reason TEXT NULL`` — free-text string carried in the
  suspend request body and displayed back to the operator on the
  next inspection. Not user-facing; the user-facing message is the
  generic "this application has been suspended" string emitted by
  the suspension-check dependency.

A separate runtime dependency (``app/core/suspension_check.py``)
inspects these columns on every write request from an API key bound
to a suspended app and raises 403. Read routes keep working — a
suspended user can still recover their own data.

No index needed: the column is read on the request hot path by PK
lookup of the ``apps`` row, which is already covered by the primary
key index. The lookup table is at most 1 row per request.

Both columns default NULL so this migration is a pure additive
change. Downgrade drops both columns; no data is preserved.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "024_app_suspension"
down_revision: Union[str, None] = "023_app_usage_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "apps",
        sa.Column(
            "suspended_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "apps",
        sa.Column(
            "suspension_reason",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("apps", "suspension_reason")
    op.drop_column("apps", "suspended_at")
