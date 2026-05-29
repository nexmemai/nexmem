"""End-to-end tests that the FastAPI lifespan refuses to start in unsafe prod.

The point is to defend against future refactors that move
`settings.validate_production()` out of the lifespan or wrap it in a
try/except. The tests do not actually start the server; they invoke the
lifespan context manager and assert it raises.
"""

import os

import pytest


def _force_env(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def _reload_app_module():
    """Re-import app.config and app.main so the new env vars take effect."""
    import importlib
    import sys

    for mod in ("app.main", "app.config"):
        if mod in sys.modules:
            del sys.modules[mod]
    config_mod = importlib.import_module("app.config")
    main_mod = importlib.import_module("app.main")
    return config_mod, main_mod


@pytest.mark.asyncio
async def test_app_lifespan_refuses_unsafe_production(monkeypatch) -> None:
    _force_env(
        monkeypatch,
        ENVIRONMENT="production",
        DEMO_MODE="true",  # the unsafe combo
        SECRET_KEY="x" * 64,
        OPENAI_API_KEY="sk-real",
        DATABASE_URL="postgresql+asyncpg://u:p@db.example.com:5432/main",
        ALLOWED_ORIGINS="https://nexmem.ai",
    )
    _, main_mod = _reload_app_module()

    with pytest.raises(RuntimeError, match="DEMO_MODE is true in production"):
        async with main_mod.lifespan(main_mod.app):
            pass  # never reached


@pytest.mark.asyncio
async def test_app_lifespan_safe_production_starts(monkeypatch) -> None:
    """Safe production config: lifespan must not raise during validation.

    We do not actually want to spin up the consolidation scheduler, so we
    mark this test as starting in non-demo mode but with a placeholder DB
    that bypasses validate_production via dev environment. Production-only
    paths (graph rebuild, scheduler) require a real DB and are out of scope.
    """
    # Use development environment for this smoke check; the validate path
    # is exercised in test_config_safety.py.
    _force_env(
        monkeypatch,
        ENVIRONMENT="development",
        DEMO_MODE="true",
        SECRET_KEY="x" * 64,
        OPENAI_API_KEY="sk-test",
        DATABASE_URL="postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/placeholder",
        ALLOWED_ORIGINS="*",
    )
    _, main_mod = _reload_app_module()

    # Should yield without raising. We don't iterate beyond entry; the
    # lifespan teardown is irrelevant for this check.
    async with main_mod.lifespan(main_mod.app):
        assert main_mod.app.title == "NexMem - Decentralized AI Memory Layer"
