"""Server-side refresh-token tracking for revocation (R-H11).

Revision ID: 014_refresh_tokens
Revises: 013_rls_users_apikeys_tokenusage
Create Date: 2026-05-22

Adds a `refresh_tokens` table to enable real session revocation:

  - logout (POST /auth/logout):     revoke one refresh token.
  - logout-all (POST /auth/logout-all): revoke every active refresh
    token for the current user.
  - rotate on use:                  the /auth/refresh endpoint marks
    the consumed token revoked and issues a new one.

The refresh JWT's `jti` claim is the UUID stored in
`refresh_tokens.id`. Tokens issued before this migration carry no
`jti` and are recognised at the route as non-revocable; they continue
to work until they expire. New tokens minted after this migration
are required to have a row.

RLS: this table is user-scoped. The migration adds the same policy
shape used for token_usage in 013.

Idempotent on re-run.
"""

from alembic import op
import sqlalchemy as sa


revision = "014_refresh_tokens"
down_revision = "013_rls_users_apikeys_tokenusage"
branch_labels = None
depends_on = None


_GUC = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ NOT NULL,
            revoked_at  TIMESTAMPTZ,
            label       TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user "
        "ON refresh_tokens (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_revoked "
        "ON refresh_tokens (user_id, revoked_at)"
    )

    # RLS — same shape as token_usage_self in migration 013.
    op.execute("ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE refresh_tokens FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS refresh_tokens_self ON refresh_tokens")
    op.execute(
        f"CREATE POLICY refresh_tokens_self ON refresh_tokens "
        f"FOR ALL USING (user_id = {_GUC}) WITH CHECK (user_id = {_GUC})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS refresh_tokens_self ON refresh_tokens")
    op.execute("ALTER TABLE refresh_tokens NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE refresh_tokens DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS idx_refresh_tokens_user_revoked")
    op.execute("DROP INDEX IF EXISTS idx_refresh_tokens_user")
    op.execute("DROP TABLE IF EXISTS refresh_tokens")
