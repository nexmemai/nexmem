"""Tests for alembic/env.py — credential safety.

These tests verify that the alembic environment refuses to run when
DATABASE_URL is unset. The previous version of env.py contained a
hardcoded production-pooler URL with an embedded password as a
fail-safe; this test guards against that regression.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / "alembic" / "env.py"


def test_env_file_has_no_hardcoded_supabase_credentials() -> None:
    """The alembic env file must not contain any literal DB credentials.

    This is a textual check on purpose. It catches the specific regression
    the audit found and also any future fail-safe override that someone
    might add later.
    """
    content = ENV_FILE.read_text()
    assert "Doesitmatter" not in content, "leaked password literal in env.py"
    assert "***REDACTED_PROJECT_ID***" not in content, "hardcoded Supabase project ref"
    assert "pooler.supabase.com" not in content, (
        "hardcoded pooler hostname; URL must come from env"
    )


def test_env_helper_resolves_database_url(monkeypatch) -> None:
    """_resolve_database_url returns the env var when set."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/db")
    spec = importlib.util.spec_from_file_location("alembic_env_helpers", ENV_FILE)
    # We can't fully import env.py because it has top-level side effects
    # (reading context.config, set_main_option, etc.). Instead, exec just the
    # helper definitions in an isolated namespace.
    src = ENV_FILE.read_text()
    # Cut off the module at the helper definition end (we keep the two helpers
    # plus the resolve call). Easiest is to read the helpers individually.
    namespace: dict = {}
    # Extract the function bodies textually.
    helper_lines = []
    capture = False
    for line in src.splitlines():
        if line.startswith("def _resolve_database_url"):
            capture = True
        if capture:
            helper_lines.append(line)
        if capture and line.startswith("def _normalise_for_alembic"):
            # Already captured the resolve helper above; stop after this
            # function definition.
            pass
        if capture and line.startswith("database_url = "):
            break

    # Append minimal stdlib imports the helpers need.
    helper_src = "import os, re, sys\n" + "\n".join(helper_lines[:-1])
    exec(helper_src, namespace)  # noqa: S102 — controlled local exec

    assert namespace["_resolve_database_url"]() == "postgresql://u:p@h:5432/db"


def test_env_helper_raises_on_missing_database_url(monkeypatch) -> None:
    """_resolve_database_url raises RuntimeError when DATABASE_URL is unset."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    src = ENV_FILE.read_text()
    helper_lines = []
    capture = False
    for line in src.splitlines():
        if line.startswith("def _resolve_database_url"):
            capture = True
        if capture:
            helper_lines.append(line)
        if capture and line.startswith("database_url = "):
            break
    helper_src = "import os, re, sys\n" + "\n".join(helper_lines[:-1])
    namespace: dict = {}
    exec(helper_src, namespace)  # noqa: S102

    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        namespace["_resolve_database_url"]()


def test_apply_migrations_script_has_no_hardcoded_credentials() -> None:
    """scripts/apply_migrations.py must read DATABASE_URL from env, not a literal."""
    script = REPO_ROOT / "scripts" / "apply_migrations.py"
    content = script.read_text()
    assert "Doesitmatter" not in content
    assert "***REDACTED_PROJECT_ID***" not in content
    # The script must reference DATABASE_URL as the source of truth.
    assert "DATABASE_URL" in content


def test_render_yaml_does_not_pin_supabase_project() -> None:
    """render.yaml must not encode any specific Supabase project ref or pooler."""
    render_yaml = (REPO_ROOT / "render.yaml").read_text()
    assert "***REDACTED_PROJECT_ID***" not in render_yaml
    assert "pooler.supabase.com" not in render_yaml


# Note: the whole-repo regression scan that used to live here was superseded
# by `tests/test_scan_secrets.py::test_repo_is_clean`, which uses the
# pattern catalogue in `scripts/scan_secrets.py` and a proper exclusion
# list (the runbook + scanner files reference the historical literal on
# purpose and were tripping the old narrow check).
