"""extend RLS to api_keys, refresh_tokens, and token_usage

Revision ID: 013_extend_rls
Revises: 012_refresh_tokens
Create Date: 2026-05-22

Phase 2 hardening (R-101):
- ``api_keys``, ``refresh_tokens``, and ``token_usage`` are user-scoped
  but did not have RLS policies. A service-role connection that
  forgot to filter by user_id could leak across tenants.
- This migration enables RLS + FORCE on those tables and binds the
  ``user_id = current_setting('app.current_user_id')`` policy.
- ``users`` itself uses ``id`` as the discriminator. Service code that
  needs to look up users from a session-less context must use a
  privileged role (``BYPASSRLS``) or the app must route those reads
  through the same context-propagation path as memory tables.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "013_extend_rls"
down_revision: Union[str, None] = "012_refresh_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENT_USER_EXPR = (
    "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)

USER_SCOPED_TABLES = (
    ("api_keys", "user_id", "api_keys_user_isolation"),
    ("refresh_tokens", "user_id", "refresh_tokens_user_isolation"),
    ("token_usage", "user_id", "token_usage_user_isolation"),
)

USERS_POLICY = "users_self_isolation"


def upgrade() -> None:
    for table, scope_col, policy in USER_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy}
            ON {table}
            FOR ALL
            USING ({scope_col} = {CURRENT_USER_EXPR})
            WITH CHECK ({scope_col} = {CURRENT_USER_EXPR})
            """
        )

    # users: a row is its own owner.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")  # lint: raw-alter-ok
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")  # lint: raw-alter-ok
    op.execute(f"DROP POLICY IF EXISTS {USERS_POLICY} ON users")
    op.execute(
        f"""
        CREATE POLICY {USERS_POLICY}
        ON users
        FOR ALL
        USING (id = {CURRENT_USER_EXPR})
        WITH CHECK (id = {CURRENT_USER_EXPR})
        """
    )

    # The login flow needs to read users by email *before* RLS context
    # is set. We add a SELECT-only policy that allows lookups when no
    # context is set (NULL). Once a session sets app.current_user_id,
    # the stricter policy above narrows access back to self. This is
    # equivalent to "anyone can attempt a login; only your own row is
    # mutable from inside an authenticated session."
    op.execute("DROP POLICY IF EXISTS users_login_lookup ON users")
    op.execute(
        f"""
        CREATE POLICY users_login_lookup
        ON users
        FOR SELECT
        USING ({CURRENT_USER_EXPR} IS NULL)
        """
    )


def downgrade() -> None:
    for table, _scope, policy in USER_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS users_login_lookup ON users")
    op.execute(f"DROP POLICY IF EXISTS {USERS_POLICY} ON users")
    op.execute("ALTER TABLE users NO FORCE ROW LEVEL SECURITY")  # lint: raw-alter-ok
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")  # lint: raw-alter-ok
