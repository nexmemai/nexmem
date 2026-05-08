"""Enable row-level security for auth and usage tables.

Revision ID: 012_auth_tables_rls
Revises: 011_fk_cascade_content_limits
Create Date: 2026-05-08
"""

from typing import Sequence, Union

from alembic import op


revision: str = "012_auth_tables_rls"
down_revision: Union[str, None] = "011_fk_cascade_content_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENT_USER_EXPR = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
AUTH_EMAIL_EXPR = "NULLIF(current_setting('app.auth_email', true), '')"
AUTH_WALLET_EXPR = "NULLIF(current_setting('app.auth_wallet_address', true), '')"
API_KEY_HASH_EXPR = "NULLIF(current_setting('app.api_key_hash', true), '')"


def upgrade() -> None:
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS users_self_select ON users")
    op.execute("DROP POLICY IF EXISTS users_self_update ON users")
    op.execute("DROP POLICY IF EXISTS users_self_delete ON users")
    op.execute("DROP POLICY IF EXISTS users_registration_insert ON users")
    op.execute("DROP POLICY IF EXISTS users_auth_lookup ON users")
    op.execute(
        f"""
        CREATE POLICY users_self_select
        ON users
        FOR SELECT
        USING (id = {CURRENT_USER_EXPR})
        """
    )
    op.execute(
        f"""
        CREATE POLICY users_self_update
        ON users
        FOR UPDATE
        USING (id = {CURRENT_USER_EXPR})
        WITH CHECK (id = {CURRENT_USER_EXPR})
        """
    )
    op.execute(
        f"""
        CREATE POLICY users_self_delete
        ON users
        FOR DELETE
        USING (id = {CURRENT_USER_EXPR})
        """
    )
    op.execute(
        """
        CREATE POLICY users_registration_insert
        ON users
        FOR INSERT
        WITH CHECK (true)
        """
    )
    op.execute(
        f"""
        CREATE POLICY users_auth_lookup
        ON users
        FOR SELECT
        USING (
            email = {AUTH_EMAIL_EXPR}
            OR wallet_address = {AUTH_WALLET_EXPR}
        )
        """
    )

    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE api_keys FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS api_keys_user_isolation ON api_keys")
    op.execute("DROP POLICY IF EXISTS api_keys_hash_lookup ON api_keys")
    op.execute(
        f"""
        CREATE POLICY api_keys_user_isolation
        ON api_keys
        FOR ALL
        USING (user_id = {CURRENT_USER_EXPR})
        WITH CHECK (user_id = {CURRENT_USER_EXPR})
        """
    )
    op.execute(
        f"""
        CREATE POLICY api_keys_hash_lookup
        ON api_keys
        FOR SELECT
        USING (key_hash = {API_KEY_HASH_EXPR})
        """
    )

    op.execute("ALTER TABLE token_usage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE token_usage FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS token_usage_user_isolation ON token_usage")
    op.execute(
        f"""
        CREATE POLICY token_usage_user_isolation
        ON token_usage
        FOR ALL
        USING (user_id = {CURRENT_USER_EXPR})
        WITH CHECK (user_id = {CURRENT_USER_EXPR})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS token_usage_user_isolation ON token_usage")
    op.execute("ALTER TABLE token_usage NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE token_usage DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS api_keys_hash_lookup ON api_keys")
    op.execute("DROP POLICY IF EXISTS api_keys_user_isolation ON api_keys")
    op.execute("ALTER TABLE api_keys NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE api_keys DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS users_auth_lookup ON users")
    op.execute("DROP POLICY IF EXISTS users_registration_insert ON users")
    op.execute("DROP POLICY IF EXISTS users_self_delete ON users")
    op.execute("DROP POLICY IF EXISTS users_self_update ON users")
    op.execute("DROP POLICY IF EXISTS users_self_select ON users")
    op.execute("ALTER TABLE users NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
