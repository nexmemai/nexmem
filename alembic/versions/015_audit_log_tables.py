"""audit log tables for gdpr + auth (P10-H1, P10-H2)

Revision ID: 015_audit_log_tables
Revises: 014_phase3_auth_hardening
Create Date: 2026-05-22

Two append-only tables that survive PR / fix / deploy churn so an
operator can answer "who did what and when" during an incident or
SOC2 audit. Both are user-scoped and protected by the same RLS
posture as the rest of the schema (forced + per-user policy +
SELECT-only lookup policy for the unauthenticated read path).

Tables:

* ``gdpr_audit_log`` — one row per /export, /delete, /consent
  action. Required for SOC2 (data subject access requests).
* ``auth_audit_log`` — login successes, login failures, password
  changes, api-key issuance/revocation, refresh-token revocation,
  email verification, password reset request/confirm. Operators
  use this for incident triage; we keep success rows too so the
  before/after picture is complete.

Both tables include ``ip_address`` and ``user_agent`` (nullable —
some events are background-tasks / scheduler initiated) plus a
``request_id`` we propagate from the structured-logging middleware
so a single audit row can be cross-referenced to the request log.

Schema choices:

* ``action`` is a free-form ``VARCHAR(64)`` rather than an enum so
  adding a new audit action does not require a migration.
* ``actor_user_id`` and ``target_user_id`` are separate columns:
  for self-acting events they are equal, for future operator
  /support-impersonation events (P11-I2) they will differ.
* ``payload`` is a small ``JSONB`` for action-specific facts (e.g.
  the api_key id that was rotated). It MUST NOT carry secrets.

Indexes are deliberately conservative — the tables grow append-only
and operators query by ``user_id + created_at`` or by
``request_id``.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "015_audit_log_tables"
down_revision: Union[str, None] = "014_phase3_auth_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENT_USER_EXPR = (
    "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)


def _create_audit_table(table: str) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.UUID(), nullable=False),
        # ``actor_user_id`` is the user who did the action. Will
        # equal ``target_user_id`` for self-actions; will differ
        # when P11-I2 (support impersonation) ships.
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        sa.Column("target_user_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",  # keep the audit row, lose the FK
            name=f"fk_{table}_actor_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            ondelete="CASCADE",  # GDPR delete cascades these too
            name=f"fk_{table}_target_user_id",
        ),
    )
    op.create_index(
        f"ix_{table}_target_user_id_created_at",
        table,
        ["target_user_id", "created_at"],
    )
    op.create_index(
        f"ix_{table}_request_id",
        table,
        ["request_id"],
    )

    # RLS: same posture as the other user-scoped tables. The
    # per-user policy keys on ``target_user_id`` so a user reads
    # their own audit trail. A null-context lookup policy lets
    # the operator-tooling path issue audit reads without
    # impersonation.
    base_policy = f"{table}_user_isolation"
    lookup_policy = f"{base_policy}_lookup"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {base_policy} ON {table}")
    op.execute(
        f"""
        CREATE POLICY {base_policy}
        ON {table}
        FOR ALL
        USING (target_user_id = {CURRENT_USER_EXPR})
        WITH CHECK (
            target_user_id = {CURRENT_USER_EXPR}
            OR actor_user_id = {CURRENT_USER_EXPR}
        )
        """
    )
    op.execute(f"DROP POLICY IF EXISTS {lookup_policy} ON {table}")
    op.execute(
        f"""
        CREATE POLICY {lookup_policy}
        ON {table}
        FOR SELECT
        USING ({CURRENT_USER_EXPR} IS NULL)
        """
    )


def upgrade() -> None:
    _create_audit_table("gdpr_audit_log")
    _create_audit_table("auth_audit_log")


def _drop_audit_table(table: str) -> None:
    base_policy = f"{table}_user_isolation"
    lookup_policy = f"{base_policy}_lookup"
    op.execute(f"DROP POLICY IF EXISTS {lookup_policy} ON {table}")
    op.execute(f"DROP POLICY IF EXISTS {base_policy} ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index(f"ix_{table}_request_id", table_name=table)
    op.drop_index(f"ix_{table}_target_user_id_created_at", table_name=table)
    op.drop_table(table)


def downgrade() -> None:
    _drop_audit_table("auth_audit_log")
    _drop_audit_table("gdpr_audit_log")
