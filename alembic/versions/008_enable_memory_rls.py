"""Enable row-level security for user-scoped memory tables.

Revision ID: 008_enable_memory_rls
Revises: 007_standardize_vector_dim
Create Date: 2026-04-30
"""

from alembic import op

revision = "008_enable_memory_rls"
down_revision = "007_standardize_vector_dim"
branch_labels = None
depends_on = None


MEMORY_TABLES = (
    ("episodic_memory", "episodic_user_isolation"),
    ("semantic_memory", "semantic_user_isolation"),
    ("procedural_memory", "procedural_user_isolation"),
    ("knowledge_nodes", "knowledge_nodes_user_isolation"),
    ("knowledge_edges", "knowledge_edges_user_isolation"),
    ("engrams", "engrams_user_isolation"),
)

CURRENT_USER_EXPR = (
    "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)


def upgrade():
    for table_name, policy_name in MEMORY_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")
        op.execute(
            f"""
            CREATE POLICY {policy_name}
            ON {table_name}
            FOR ALL
            USING (user_id = {CURRENT_USER_EXPR})
            WITH CHECK (user_id = {CURRENT_USER_EXPR})
            """
        )


def downgrade():
    for table_name, policy_name in reversed(MEMORY_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
