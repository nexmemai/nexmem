"""Migration 007 safety tests — destructive DELETE is gated.

Migration `007_standardize_vector_dim` previously ran
`DELETE FROM semantic_memory` unconditionally on every upgrade. These
tests guard against the regression.

Three layers of verification:

1. Textual contract: the `DELETE FROM semantic_memory` statement must
   only appear inside an `ALLOW_DESTRUCTIVE_MIGRATION` branch, never at
   module top level.
2. Behavioural contract: when the migration's `upgrade()` is called via
   a stub `op` and the current dim is already 384, the recorded SQL
   must not include any DELETE.
3. Safety contract: when the dim differs and `ALLOW_DESTRUCTIVE_MIGRATION`
   is not set, `upgrade()` must raise RuntimeError before issuing any DELETE.

We stub the alembic `op` module rather than running against a real
Postgres so this test runs in any CI without pgvector.
"""

from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = REPO_ROOT / "alembic" / "versions" / "007_standardize_vector_dim.py"


# ── 1. Textual contract ──────────────────────────────────────────────────────

def test_no_unconditional_delete_at_module_top_level() -> None:
    """The DELETE statement must not appear outside a function body, and the
    `upgrade()` function must read ALLOW_DESTRUCTIVE_MIGRATION before executing
    a DELETE statement.

    We look only at executable `op.execute(...)` lines so the textual mention
    of DELETE in the file's docstring/comments does not trip the check.
    """
    src = MIGRATION_PATH.read_text()

    # Destructive path must still be reachable on consent.
    assert "DELETE FROM semantic_memory" in src

    upgrade_idx = src.index("def upgrade(")

    # All op.execute() calls that contain DELETE must appear inside a function
    # body, never at module scope.
    import re
    for m in re.finditer(r'op\.execute\([^)]*DELETE[^)]*\)', src, flags=re.DOTALL):
        assert m.start() > upgrade_idx, (
            "op.execute(DELETE ...) must live inside upgrade()/downgrade(), "
            "not at module scope"
        )

    # The destructive path must reference the env var by name.
    assert "ALLOW_DESTRUCTIVE_MIGRATION" in src


# ── Helpers to drive the migration without a live DB ────────────────────────

def _load_migration_module() -> ModuleType:
    """Import migration 007 in a way that does not require alembic context.

    We pre-stub the `alembic.op` symbol with a recorder so the module body's
    use of `op.execute(...)` is captured.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("mig_007", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["mig_007"] = module
    spec.loader.exec_module(module)
    return module


class _OpRecorder:
    """Minimal stand-in for alembic.op for tests."""

    def __init__(self, current_dim: int | None, row_count: int = 0):
        self.executed_sql: list[str] = []
        self.altered_columns: list[tuple] = []
        self._current_dim = current_dim
        self._row_count = row_count

    # ── alembic.op API surface used by the migration ────────────────────────
    def execute(self, sql) -> None:
        self.executed_sql.append(str(sql))

    def alter_column(self, table: str, column: str, **kwargs) -> None:
        self.altered_columns.append((table, column, kwargs))

    def get_bind(self):
        # The migration's helpers use `op.get_bind().execute(text(...))`.
        # We return a fake bind whose `execute` returns canned results matching
        # the queries the migration runs.
        recorder = self

        class _FakeResult:
            def __init__(self, rows):
                self._rows = rows

            def first(self):
                return self._rows[0] if self._rows else None

            def scalar(self):
                return self._rows[0][0] if self._rows else None

        class _FakeBind:
            def execute(self, stmt):
                sql = str(stmt)
                if "atttypmod" in sql:
                    if recorder._current_dim is None:
                        return _FakeResult([])
                    return _FakeResult([(recorder._current_dim,)])
                if "COUNT(*) FROM semantic_memory" in sql:
                    return _FakeResult([(recorder._row_count,)])
                return _FakeResult([])

        return _FakeBind()


# ── 2. Behavioural contract: already-384 path is non-destructive ─────────────

def test_upgrade_already_at_target_dim_does_not_delete() -> None:
    module = _load_migration_module()
    recorder = _OpRecorder(current_dim=384, row_count=42)
    with mock.patch.object(module, "op", recorder):
        module.upgrade()

    joined = " | ".join(recorder.executed_sql)
    assert "DELETE FROM semantic_memory" not in joined, (
        "non-destructive path must skip DELETE entirely"
    )
    # Index recreation should still happen.
    assert any("CREATE INDEX ix_semantic_vector_hnsw" in s for s in recorder.executed_sql)
    assert recorder.altered_columns, "embedding_model default should still be aligned"


# ── 3. Safety contract: differing dim without consent must raise ─────────────

def test_upgrade_differing_dim_without_consent_raises(monkeypatch) -> None:
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_MIGRATION", raising=False)
    module = _load_migration_module()
    recorder = _OpRecorder(current_dim=1536, row_count=100)
    with mock.patch.object(module, "op", recorder):
        with pytest.raises(RuntimeError) as exc:
            module.upgrade()

    msg = str(exc.value)
    assert "vector(1536)" in msg
    assert "vector(384)" in msg
    assert "100" in msg, "row count must be reported so operator sees the impact"
    assert "ALLOW_DESTRUCTIVE_MIGRATION" in msg

    # Critically: no DELETE was issued before the raise.
    assert not any("DELETE FROM semantic_memory" in s for s in recorder.executed_sql), (
        "destructive statement was issued before consent gate"
    )


def test_upgrade_differing_dim_with_consent_executes_destructive_path(monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_MIGRATION", "1")
    module = _load_migration_module()
    recorder = _OpRecorder(current_dim=1536, row_count=100)
    with mock.patch.object(module, "op", recorder):
        module.upgrade()

    joined = " | ".join(recorder.executed_sql)
    assert "DELETE FROM semantic_memory" in joined
    assert "ALTER TABLE semantic_memory ALTER COLUMN vector TYPE vector(384)" in joined
    assert "CREATE INDEX ix_semantic_vector_hnsw" in joined


def test_downgrade_already_at_target_dim_does_not_delete() -> None:
    module = _load_migration_module()
    recorder = _OpRecorder(current_dim=1536, row_count=10)
    with mock.patch.object(module, "op", recorder):
        module.downgrade()

    assert not any("DELETE FROM semantic_memory" in s for s in recorder.executed_sql)


def test_downgrade_without_consent_raises(monkeypatch) -> None:
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_MIGRATION", raising=False)
    module = _load_migration_module()
    recorder = _OpRecorder(current_dim=384, row_count=10)
    with mock.patch.object(module, "op", recorder):
        with pytest.raises(RuntimeError) as exc:
            module.downgrade()

    assert "ALLOW_DESTRUCTIVE_MIGRATION" in str(exc.value)
    assert not any("DELETE FROM semantic_memory" in s for s in recorder.executed_sql)
