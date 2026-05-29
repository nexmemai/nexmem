"""Source contract for `scripts/run_migrations.py` (R-H10).

The script must:
  - acquire pg_advisory_lock BEFORE invoking alembic.command.upgrade
  - release pg_advisory_unlock AFTER the upgrade (success or failure)
  - use a single fixed lock key (so all replicas serialise on the
    same key)
  - refuse to run when DATABASE_URL is unset

These are textual / structural checks. A live concurrency test
against a real Postgres is out of scope for unit CI; the
integration-tests job already runs the script as part of the
Alembic-head migration step (via tests/integration/conftest.py).

If a future change accidentally inverts the lock / upgrade order
or removes the unlock-in-finally, this test fails.
"""

from __future__ import annotations

import re
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_migrations.py"


def test_script_uses_advisory_lock_around_alembic_upgrade() -> None:
    src = SCRIPT.read_text()
    # The lock acquisition must appear BEFORE command.upgrade,
    # and the unlock must appear in a try/finally around the upgrade.
    lock_idx = src.index("pg_advisory_lock(")
    upgrade_idx = src.index("command.upgrade(")
    unlock_idx = src.index("pg_advisory_unlock(")
    finally_idx = src.index("finally:")
    assert lock_idx < upgrade_idx < finally_idx < unlock_idx, (
        "Order violated: pg_advisory_lock must precede command.upgrade, "
        "and pg_advisory_unlock must live inside a `finally` block "
        "after the upgrade.\n"
        f"  lock @ {lock_idx}, upgrade @ {upgrade_idx}, "
        f"finally @ {finally_idx}, unlock @ {unlock_idx}"
    )


def test_script_uses_single_fixed_lock_key() -> None:
    src = SCRIPT.read_text()
    # Find every reference passed to pg_advisory_lock / unlock. The script
    # uses an f-string against ADVISORY_LOCK_KEY, so we accept either the
    # literal int or the constant name.
    keys = set(
        re.findall(
            r"pg_advisory_(?:un)?lock\(\{([A-Za-z_][A-Za-z0-9_]*)\}\)|"
            r"pg_advisory_(?:un)?lock\(([0-9_]+)\)",
            src,
        )
    )
    # Flatten the (group1, group2) tuples; one of the two is empty per match.
    keys = {a or b for a, b in keys}
    assert keys, "no pg_advisory_lock(<int|VAR>) call found"
    assert len(keys) == 1, (
        f"expected a single fixed lock key, got {keys}. All replicas must "
        f"serialise on the same key; otherwise the lock is useless."
    )
    # Also confirm the constant ADVISORY_LOCK_KEY exists and is an int literal.
    if list(keys)[0].isidentifier():
        constant_match = re.search(
            rf"^{list(keys)[0]}\s*=\s*([0-9_]+)", src, flags=re.MULTILINE
        )
        assert constant_match, (
            f"{list(keys)[0]} is referenced in the lock call but not defined "
            f"as a numeric constant in the script."
        )


def test_script_refuses_without_database_url(monkeypatch) -> None:
    """Importing and calling _resolve_db_url with no env var must SystemExit(2)."""
    import importlib.util
    import sys

    monkeypatch.delenv("DATABASE_URL", raising=False)
    spec = importlib.util.spec_from_file_location("run_migrations_mod", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_migrations_mod"] = module
    spec.loader.exec_module(module)

    import pytest

    with pytest.raises(SystemExit) as exc:
        module._resolve_db_url()
    assert exc.value.code == 2


def test_dockerfile_invokes_run_migrations() -> None:
    df = Path(__file__).resolve().parents[1] / "Dockerfile"
    text = df.read_text()
    assert "scripts/run_migrations.py" in text, (
        "Dockerfile CMD must call scripts/run_migrations.py before uvicorn; "
        "otherwise the migration race (R-H10) returns."
    )
    # Make sure we did not also leave the legacy direct invocation.
    assert "alembic upgrade head &&" not in text, (
        "Dockerfile still has the legacy 'alembic upgrade head &&' wrapper; "
        "remove it — run_migrations.py is the only entry point now."
    )


def test_render_yaml_invokes_run_migrations() -> None:
    rf = Path(__file__).resolve().parents[1] / "render.yaml"
    text = rf.read_text()
    assert "scripts/run_migrations.py" in text, (
        "render.yaml startCommand must call scripts/run_migrations.py."
    )
    assert "alembic upgrade head &&" not in text
