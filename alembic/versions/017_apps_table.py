"""apps table — multi-tenant app model (P4-B1).

Revision ID: 017_apps_table
Revises: 016_jsonb_shape_checks
Create Date: 2026-05-23

Phase 4 — Apps as a first-class entity.

Schema
------
    apps(
        id           UUID PRIMARY KEY,
        user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name         VARCHAR(100) NOT NULL,
        description  TEXT NULL,
        is_active    BOOLEAN NOT NULL DEFAULT TRUE,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );

RLS posture
-----------
We enable RLS + FORCE on ``apps`` and bind the same
``user_id = current_setting('app.current_user_id')`` policy used by the
rest of the schema (see migration 008 for memory tables and migration
013 for ``users``/``api_keys``/``refresh_tokens``/``token_usage``).

This is the *user-scoped* policy on the ``apps`` row itself ("a user
sees only their own apps"). The *app-scoped* policy on memory tables
("a request only sees rows for the active app") is separate and lives
in migration 019 (P4-B4).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "017_apps_table"
down_revision: Union[str, None] = "016_jsonb_shape_checks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENT_USER_EXPR = (
    "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_apps_user_id",
        ),
    )
    op.create_index("ix_apps_user_id", "apps", ["user_id"])

    # RLS — user-scoped (a user sees only their own apps).
    op.execute("ALTER TABLE apps ENABLE ROW LEVEL SECURITY")  # lint: raw-alter-ok
    op.execute("ALTER TABLE apps FORCE ROW LEVEL SECURITY")  # lint: raw-alter-ok
    op.execute("DROP POLICY IF EXISTS apps_user_isolation ON apps")
    op.execute(
        f"""
        CREATE POLICY apps_user_isolation
        ON apps
        FOR ALL
        USING (user_id = {CURRENT_USER_EXPR})
        WITH CHECK (user_id = {CURRENT_USER_EXPR})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS apps_user_isolation ON apps")
    op.execute("ALTER TABLE apps NO FORCE ROW LEVEL SECURITY")  # lint: raw-alter-ok
    op.execute("ALTER TABLE apps DISABLE ROW LEVEL SECURITY")  # lint: raw-alter-ok
    op.drop_index("ix_apps_user_id", table_name="apps")
    op.drop_table("apps")  # lint: drop-table-ok
