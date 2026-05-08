"""Migration verification tests for auth table RLS policies."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "012_auth_tables_rls.py"


def _migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_auth_tables_enable_and_force_rls():
    sql = _migration_sql()

    for table in ("users", "api_keys", "token_usage"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql


def test_api_keys_are_isolated_by_current_user_and_hash_lookup_only():
    sql = _migration_sql()

    assert "CREATE POLICY api_keys_user_isolation" in sql
    assert "ON api_keys" in sql
    assert "USING (user_id = {CURRENT_USER_EXPR})" in sql
    assert "WITH CHECK (user_id = {CURRENT_USER_EXPR})" in sql
    assert "CREATE POLICY api_keys_hash_lookup" in sql
    assert "USING (key_hash = {API_KEY_HASH_EXPR})" in sql


def test_token_usage_is_isolated_by_current_user():
    sql = _migration_sql()

    assert "CREATE POLICY token_usage_user_isolation" in sql
    assert "ON token_usage" in sql
    assert "USING (user_id = {CURRENT_USER_EXPR})" in sql
    assert "WITH CHECK (user_id = {CURRENT_USER_EXPR})" in sql


def test_users_has_self_policy_and_limited_auth_lookup_policy():
    sql = _migration_sql()

    assert "CREATE POLICY users_self_select" in sql
    assert "USING (id = {CURRENT_USER_EXPR})" in sql
    assert "CREATE POLICY users_auth_lookup" in sql
    assert "email = {AUTH_EMAIL_EXPR}" in sql
    assert "wallet_address = {AUTH_WALLET_EXPR}" in sql
    assert "CREATE POLICY users_registration_insert" in sql
    assert "USING (true)" not in sql
