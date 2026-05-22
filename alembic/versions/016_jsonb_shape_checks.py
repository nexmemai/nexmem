"""json shape CHECK constraints (P5-C9)

Revision ID: 016_jsonb_shape_checks
Revises: 015_audit_log_tables
Create Date: 2026-05-22

Several columns are typed ``JSONB`` and used as ``{}`` or ``[]`` by
the application but Postgres has no opinion: a row that lands with a
JSON scalar (``42``, ``"foo"``, ``null``) survives the INSERT and
breaks every consumer that expects to call ``.keys()`` on it. CHECK
constraints add a thin layer of "did the application uphold the
contract?" at the database boundary.

Constraints added:

* ``ProceduralMemory.settings``        -> object only (or NULL).
* ``ProceduralMemory.workflows``       -> array only (or NULL).
* ``EpisodicMemory.metadata``          -> object only (or NULL).
* ``EpisodicMemory.tags``               -> array only (or NULL).
* ``SemanticMemory.metadata``          -> object only (or NULL).
* ``KnowledgeNode.properties``         -> object only (or NULL).
* ``KnowledgeEdge.extra_metadata``     -> object only (or NULL).
* ``Engram.salience_scores``           -> object only.
* ``Engram.entities``/``actions``/``objects``/``negated_actions``
  /``connections``                     -> array only.
* ``GDPRAuditLog.payload`` /
  ``AuthAuditLog.payload``             -> object only.

We use ``ALTER TABLE ... ADD CONSTRAINT ... NOT VALID`` so the
migration does not block on a full table scan, then ``VALIDATE
CONSTRAINT`` separately. New rows are checked immediately. Existing
rows that violate the contract surface during ``VALIDATE`` and the
operator can clean them up before re-running.

Idempotent: ``DROP CONSTRAINT IF EXISTS`` first.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "016_jsonb_shape_checks"
down_revision: Union[str, None] = "015_audit_log_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, kind) where kind in {"object", "array"}.
# Setting kind="object_or_null" allows NULL to be present (most of
# our JSONB columns are nullable).
_CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    ("procedural_memory", "settings", "object"),
    ("procedural_memory", "workflows", "array"),
    ("episodic_memory", "metadata", "object"),
    ("episodic_memory", "tags", "array"),
    ("semantic_memory", "metadata", "object"),
    ("knowledge_nodes", "properties", "object"),
    ("knowledge_edges", "extra_metadata", "object"),
    ("engrams", "salience_scores", "object"),
    ("engrams", "entities", "array"),
    ("engrams", "actions", "array"),
    ("engrams", "objects", "array"),
    ("engrams", "negated_actions", "array"),
    ("engrams", "connections", "array"),
    ("gdpr_audit_log", "payload", "object"),
    ("auth_audit_log", "payload", "object"),
)


def _check_expr(column: str, kind: str) -> str:
    # ``jsonb_typeof(NULL)`` is NULL which is treated as TRUE in a
    # CHECK constraint, so nullable columns are tolerated.
    return f"jsonb_typeof({column}) = '{kind}' OR {column} IS NULL"


def _constraint_name(table: str, column: str) -> str:
    return f"ck_{table}_{column}_jsonb_shape"


def upgrade() -> None:
    for table, column, kind in _CONSTRAINTS:
        name = _constraint_name(table, column)
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}')
        op.execute(
            f'ALTER TABLE {table} '
            f'ADD CONSTRAINT {name} '
            f'CHECK ({_check_expr(column, kind)}) '
            f'NOT VALID'
        )
        # Validate as a separate step so the ADD takes only an
        # ACCESS EXCLUSIVE lock briefly. ``VALIDATE`` then takes a
        # SHARE UPDATE EXCLUSIVE lock and runs without blocking
        # SELECTs.
        op.execute(f'ALTER TABLE {table} VALIDATE CONSTRAINT {name}')


def downgrade() -> None:
    for table, column, _kind in _CONSTRAINTS:
        name = _constraint_name(table, column)
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}')
