"""Per-app usage tracking (P4-B5, Block 7).

Revision ID: 023_app_usage_tracking
Revises: 022_user_soft_delete
Create Date: 2026-05-23

Phase 4 — app-level metrics + quotas.

Adds a single ``app_usage`` table that stores one row per
``(app_id, month_year)`` pair. Two counters live on the row —
``write_count`` and ``read_count`` — and are bumped via a
single-statement ``INSERT ... ON CONFLICT DO UPDATE`` upsert from
``app/services/app_quota.py``. Per-month rollover is implicit: a
new month_year string yields a new row, not an UPDATE on the
prior one.

Schema
------
    app_usage(
        id            UUID PRIMARY KEY,
        app_id        UUID NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
        user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        month_year    VARCHAR(7) NOT NULL,           -- e.g. "2026-05"
        write_count   INTEGER NOT NULL DEFAULT 0,
        read_count    INTEGER NOT NULL DEFAULT 0,
        last_updated  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

Constraints + indexes
---------------------
- UNIQUE (app_id, month_year): the upsert anchor. Without this the
  ``ON CONFLICT (app_id, month_year)`` clause cannot resolve.
- INDEX on (user_id, month_year): supports the dashboard query
  "give me every app's usage for user X in this month".

RLS
---
- ENABLE ROW LEVEL SECURITY + FORCE.
- Single policy ``app_usage_user_isolation`` matching the rest of
  the schema: ``user_id = current_setting('app.current_user_id')``.
  The ``apps`` row's user FK is the source of truth; the ``user_id``
  column on ``app_usage`` is denormalised so the RLS policy can
  match without a join (the same shape used by every other
  user-scoped table since migration 008).

Why both a user_id AND an app_id column
---------------------------------------
``app_id`` is the upsert key. ``user_id`` is the RLS key plus the
secondary-index key. Storing both lets the upsert avoid a join and
lets RLS run on a single column lookup. The redundancy is worth
the index footprint at our scale.

Downgrade drops everything (table, indexes, policy). Nothing else
references this table today.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "023_app_usage_tracking"
down_revision: Union[str, None] = "022_user_soft_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENT_USER_EXPR = (
    "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "app_usage",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("app_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("month_year", sa.String(length=7), nullable=False),
        sa.Column(
            "write_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "read_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["apps.id"],
            ondelete="CASCADE",
            name="fk_app_usage_app_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_app_usage_user_id",
        ),
        sa.UniqueConstraint(
            "app_id", "month_year", name="uq_app_usage_app_month"
        ),
    )
    op.create_index(
        "ix_app_usage_user_month",
        "app_usage",
        ["user_id", "month_year"],
    )

    # RLS posture mirrors every other user-scoped table.
    op.execute("ALTER TABLE app_usage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app_usage FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS app_usage_user_isolation ON app_usage")
    op.execute(
        f"""
        CREATE POLICY app_usage_user_isolation
        ON app_usage
        FOR ALL
        USING (user_id = {CURRENT_USER_EXPR})
        WITH CHECK (user_id = {CURRENT_USER_EXPR})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS app_usage_user_isolation ON app_usage")
    op.execute("ALTER TABLE app_usage NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app_usage DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_app_usage_user_month", table_name="app_usage")
    op.drop_table("app_usage")
