"""api_keys.app_id FK to apps.id (P4-B2).

Revision ID: 018_apikeys_app_fk
Revises: 017_apps_table
Create Date: 2026-05-23

Phase 4 — bind api_keys to first-class apps.

Why
---
Today, app scoping is encoded in ``api_keys.scopes`` as the substring
``app:<uuid>``. This is brittle (no FK, no rename, no rotation, no
cross-table joins) and was flagged as R-203 / P4-B2 in the hardening
plan.

Schema change
-------------
- ADD COLUMN ``api_keys.app_id UUID NULL REFERENCES apps(id) ON DELETE
  SET NULL``. Existing rows are backfilled to NULL — re-binding an
  existing key to an Apps row is an explicit operator action and is
  not part of this migration.
- INDEX on ``app_id`` for the lookup paths added in later phases.
- ON DELETE SET NULL (not CASCADE) so deleting an App does not destroy
  audit-relevant API keys; the operator can re-bind or revoke later.

Two-deploy roll
---------------
Per the Phase 3+ plan, this is the additive half. A future migration
(after a fully deployed code-side switchover) will deprecate / drop
the ``scopes`` substring encoding.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "018_apikeys_app_fk"
down_revision: Union[str, None] = "017_apps_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("app_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_api_keys_app_id",
        "api_keys",
        ["app_id"],
    )
    op.create_foreign_key(
        "fk_api_keys_app_id",
        source_table="api_keys",
        referent_table="apps",
        local_cols=["app_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    # Backfill is explicitly a no-op: the column is NULLable and existing
    # rows remain NULL until an operator binds them.


def downgrade() -> None:
    op.drop_constraint(
        "fk_api_keys_app_id", "api_keys", type_="foreignkey"
    )
    op.drop_index("ix_api_keys_app_id", table_name="api_keys")
    op.drop_column("api_keys", "app_id")
