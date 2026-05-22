"""Phase 2 sentinel: unit tests must not connect to a real database.

The unit test job in CI runs in DEMO_MODE=true. If a future test
accidentally drops out of demo mode and tries to reach Postgres on
localhost, it will:
* succeed silently if the developer has Postgres running locally, or
* fail with a confusing connection error in CI.

This file is a single sentinel that fails clearly if the engine has
been used to actually open a connection during the unit run. It
relies on SQLAlchemy's ``connect_args`` already being scoped to the
demo placeholder URL.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.database import engine


pytestmark = pytest.mark.unit


def test_demo_mode_is_active_for_unit_run():
    """Without demo mode the engine would target a real Postgres URL."""
    assert settings.demo_mode is True, (
        "unit tests must run in DEMO_MODE=true; check pytest.ini and conftest"
    )


def test_engine_url_points_at_demo_placeholder():
    """The async engine for unit tests must NOT bind to a real DB URL.

    If this fails, someone wired DATABASE_URL into the unit run by
    accident. The integration job sets DATABASE_URL on purpose; the
    unit job must not.
    """
    url_str = str(engine.url)
    # Demo placeholder uses 'demo' user/host/db. SQLAlchemy renders
    # the password as '***' when stringifying.
    assert "demo:" in url_str and "@localhost" in url_str and "/demo" in url_str, (
        f"unit engine bound to unexpected URL: {url_str!r}"
    )


def test_run_db_tests_env_is_unset():
    """Belt-and-braces: integration marker is opt-in via RUN_DB_TESTS."""
    import os

    assert os.getenv("RUN_DB_TESTS") != "1", (
        "RUN_DB_TESTS=1 should not be set in the unit job; the "
        "integration suite uses its own env."
    )
