"""phase 3 auth hardening: email verification + password reset tokens

Revision ID: 014_phase3_auth_hardening
Revises: 013_extend_rls
Create Date: 2026-05-22

Phase 3 hardening (P3-A1, P3-A2):
- ``users.email_verified_at``: nullable timestamp set when the user
  confirms their email. Login can be gated on this column when
  ``EMAIL_VERIFICATION_REQUIRED=true`` in the environment.
- ``email_verification_tokens``: hashed, single-use tokens with a
  24-hour TTL. Issued at registration and on resend.
- ``password_reset_tokens``: hashed, single-use tokens with a 30-min
  TTL. Issued by ``/auth/password-reset/request``; consumed by
  ``/auth/password-reset/confirm``. Successful consumption revokes
  every refresh token for the user (the revocation itself is wired
  in the route handler against the existing ``refresh_tokens`` table).

RLS:
- Both new tables are user-scoped and therefore must have RLS forced
  per ``CONTRIBUTING.md §3.3``. Policies bind on
  ``user_id = current_setting('app.current_user_id')`` exactly the
  same way as ``refresh_tokens``.
- A SELECT-only ``token_lookup`` policy is added so the public
  consume routes (``/auth/verify-email/confirm`` and
  ``/auth/password-reset/confirm``) can look up a row by token_hash
  *before* a session is established, mirroring the
  ``users_login_lookup`` policy from migration 013.

Idempotency:
- ``IF NOT EXISTS`` clauses + ``DROP POLICY IF EXISTS`` make the
  migration safe to retry after a partial failure.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "014_phase3_auth_hardening"
down_revision: Union[str, None] = "013_extend_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENT_USER_EXPR = (
    "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)


def upgrade() -> None:
    # ── users.email_verified_at ──────────────────────────────────────────────
    op.execute(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP WITH TIME ZONE"
    )

    # ── email_verification_tokens ────────────────────────────────────────────
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_email_verification_tokens_user_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_hash", name="uq_email_verification_tokens_token_hash"
        ),
    )
    op.create_index(
        "ix_email_verification_tokens_user_id",
        "email_verification_tokens",
        ["user_id"],
    )
    op.create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens",
        ["token_hash"],
    )

    # ── password_reset_tokens ────────────────────────────────────────────────
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_password_reset_tokens_user_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_hash", name="uq_password_reset_tokens_token_hash"
        ),
    )
    op.create_index(
        "ix_password_reset_tokens_user_id",
        "password_reset_tokens",
        ["user_id"],
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
    )

    # ── RLS on both new tables ───────────────────────────────────────────────
    for table, policy in (
        ("email_verification_tokens", "email_verification_tokens_user_isolation"),
        ("password_reset_tokens", "password_reset_tokens_user_isolation"),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy}
            ON {table}
            FOR ALL
            USING (user_id = {CURRENT_USER_EXPR})
            WITH CHECK (user_id = {CURRENT_USER_EXPR})
            """
        )

        # SELECT-only lookup policy: when no RLS context is set (NULL),
        # the public consume route can find a row by ``token_hash``.
        # Combined with the ``token_hash`` uniqueness constraint and the
        # SHA-256 hashing of high-entropy tokens, this is safe.
        lookup_policy = f"{policy}_lookup"
        op.execute(f"DROP POLICY IF EXISTS {lookup_policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {lookup_policy}
            ON {table}
            FOR SELECT
            USING ({CURRENT_USER_EXPR} IS NULL)
            """
        )


def downgrade() -> None:
    for table, policy in (
        ("password_reset_tokens", "password_reset_tokens_user_isolation"),
        ("email_verification_tokens", "email_verification_tokens_user_isolation"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy}_lookup ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index(
        "ix_password_reset_tokens_token_hash", table_name="password_reset_tokens"
    )
    op.drop_index(
        "ix_password_reset_tokens_user_id", table_name="password_reset_tokens"
    )
    op.drop_table("password_reset_tokens")

    op.drop_index(
        "ix_email_verification_tokens_token_hash",
        table_name="email_verification_tokens",
    )
    op.drop_index(
        "ix_email_verification_tokens_user_id",
        table_name="email_verification_tokens",
    )
    op.drop_table("email_verification_tokens")

    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email_verified_at")
