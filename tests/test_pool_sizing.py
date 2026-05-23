"""P5-C2 — connection-pool sizing must come from settings, not hardcoded.

This test exists to catch the regression where the engine kwargs in
``app/database.py::_build_engine_kwargs`` lose touch with the values
in ``app/config.py``. The Render × Supabase math documented on
``settings.db_pool_size`` only holds if every component reads from
the same source — a hardcoded ``pool_timeout=30`` in the engine code
would silently break that math when an operator sets
``DB_POOL_TIMEOUT=60`` in Render env vars.
"""
from __future__ import annotations

import pytest

from app import database
from app.config import settings


pytestmark = [pytest.mark.unit]


def test_engine_kwargs_use_settings_for_pool_config():
    """Every pool-related kwarg the engine receives comes from settings."""
    kwargs = database._build_engine_kwargs()

    assert kwargs["pool_size"] == settings.db_pool_size, (
        "pool_size diverged from settings.db_pool_size — Render/Supabase "
        "connection-budget math will break"
    )
    assert kwargs["max_overflow"] == settings.db_max_overflow, (
        "max_overflow diverged from settings.db_max_overflow"
    )
    assert kwargs["pool_timeout"] == settings.db_pool_timeout, (
        "pool_timeout is hardcoded — operator cannot tune via DB_POOL_TIMEOUT"
    )
    assert kwargs["pool_recycle"] == settings.db_pool_recycle, (
        "pool_recycle is hardcoded — operator cannot tune via DB_POOL_RECYCLE"
    )


def test_default_pool_sizing_matches_render_supabase_math():
    """Defaults must satisfy the documented connection-budget math.

    Math in ``app/config.py`` says:
        Render free tier: 1 worker, Supabase free: 20 max connections.
        Pool size 5 + max overflow 10 = 15 max per worker.
        Leave 5 for admin/migrations. Total = 20. Safe.

    A change to either default that breaks this should explicitly bump
    both the value AND the comment.
    """
    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 10
    assert settings.db_pool_timeout == 30
    assert settings.db_pool_recycle == 3600

    # Connection-budget math: must stay <= Supabase free tier limit.
    SUPABASE_FREE_MAX_CLIENT_CONN = 20
    ADMIN_RESERVE = 5
    per_worker = settings.db_pool_size + settings.db_max_overflow
    assert per_worker + ADMIN_RESERVE <= SUPABASE_FREE_MAX_CLIENT_CONN, (
        f"Default pool sizing ({per_worker} per worker + {ADMIN_RESERVE} "
        f"admin reserve) exceeds Supabase free tier ceiling "
        f"({SUPABASE_FREE_MAX_CLIENT_CONN}). Update the math comment in "
        f"app/config.py if this is intentional."
    )
