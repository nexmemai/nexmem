"""P5-C6: scripts/lint_migrations.py rule tests.

Each test writes a tiny migration to a temporary directory and asserts
that the linter flags (or accepts) it. The grandfathered migrations
in alembic/versions/ are NOT exercised — the CI step diffs against
``origin/main`` so those don't trip the gate.
"""
from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit]


def _run(tmp_path: Path, body: str) -> tuple[int, list[str]]:
    p = tmp_path / "migration.py"
    p.write_text(body)
    from scripts.lint_migrations import lint_file

    findings = lint_file(p)
    return (1 if findings else 0), findings


_HAPPY = '''"""ok migration"""
from alembic import op


def upgrade() -> None:
    op.add_column("users", sa.Column("foo", sa.String()))


def downgrade() -> None:
    op.drop_column("users", "foo")
'''


def test_clean_migration_passes(tmp_path):
    rc, findings = _run(tmp_path, _HAPPY)
    assert rc == 0, findings


def test_unconditional_delete_from_flagged(tmp_path):
    body = '''"""bad migration"""
from alembic import op


def upgrade() -> None:
    op.execute("DELETE FROM users")


def downgrade() -> None:
    op.execute("SELECT 1")
'''
    rc, findings = _run(tmp_path, body)
    assert rc == 1
    assert any("DELETE FROM" in f for f in findings)


def test_truncate_flagged(tmp_path):
    body = '''"""bad migration"""
from alembic import op


def upgrade() -> None:
    op.execute("TRUNCATE users")


def downgrade() -> None:
    op.execute("-- noop")
'''
    rc, findings = _run(tmp_path, body)
    assert rc == 1
    assert any("TRUNCATE" in f for f in findings)


def test_drop_table_flagged_without_opt_out(tmp_path):
    body = '''"""bad migration"""
from alembic import op


def upgrade() -> None:
    op.create_table("foo")


def downgrade() -> None:
    op.drop_table("foo")
'''
    rc, findings = _run(tmp_path, body)
    assert rc == 1
    assert any("op.drop_table" in f or "drop_table-ok" in f for f in findings)


def test_drop_table_with_opt_out_passes(tmp_path):
    body = '''"""ok migration"""
from alembic import op


def upgrade() -> None:
    op.create_table("foo")


def downgrade() -> None:
    op.drop_table("foo")  # lint: drop-table-ok
'''
    rc, findings = _run(tmp_path, body)
    assert rc == 0, findings


def test_pass_only_downgrade_flagged(tmp_path):
    body = '''"""bad migration"""
from alembic import op


def upgrade() -> None:
    op.add_column("users", sa.Column("x", sa.String()))


def downgrade() -> None:
    pass
'''
    rc, findings = _run(tmp_path, body)
    assert rc == 1
    assert any("downgrade" in f for f in findings)


def test_missing_downgrade_flagged(tmp_path):
    body = '''"""bad migration"""
from alembic import op


def upgrade() -> None:
    op.add_column("users", sa.Column("x", sa.String()))
'''
    rc, findings = _run(tmp_path, body)
    assert rc == 1
    assert any("downgrade" in f for f in findings)


def test_raw_alter_with_opt_out_passes(tmp_path):
    body = '''"""rls migration"""
from alembic import op


def upgrade() -> None:
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")  # lint: raw-alter-ok


def downgrade() -> None:
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")  # lint: raw-alter-ok
'''
    rc, findings = _run(tmp_path, body)
    assert rc == 0, findings


def test_real_migration_016_passes():
    """Sanity: the migration this PR ships must lint cleanly."""
    from scripts.lint_migrations import lint_file

    findings = lint_file(Path("alembic/versions/016_jsonb_shape_checks.py"))
    assert findings == []
