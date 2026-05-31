"""Phase 2 unit tests for app.config.Settings.validate_production().

Phase 1 only logged warnings on insecure config. Phase 2 RAISES.
These tests pin that contract so a future change cannot silently
relax startup safety.
"""
from __future__ import annotations

import pytest

from app.config import Settings


pytestmark = pytest.mark.unit


def _base_kwargs(**overrides):
    base = dict(
        demo_mode=False,
        database_url="postgresql+asyncpg://user:pw@localhost:5432/db",
        secret_key="x" * 64,
        openai_api_key="sk-test-realistic-shape-xyzxyzxyzxyzxyzxyzxyz123456",
        allowed_origins=["https://nexmem.example.com"],
        redis_url="redis://localhost:6379/0",
    )
    base.update(overrides)
    return base


def test_demo_mode_skips_validation():
    Settings(demo_mode=True).validate_production()  # should not raise


def test_missing_database_url_raises_in_production():
    with pytest.raises(RuntimeError) as exc:
        Settings(**_base_kwargs(database_url="")).validate_production()
    assert "DATABASE_URL" in str(exc.value)


def test_default_secret_key_raises():
    with pytest.raises(RuntimeError) as exc:
        Settings(
            **_base_kwargs(secret_key="local-dev-secret-change-this-before-production")
        ).validate_production()
    assert "SECRET_KEY" in str(exc.value)


def test_short_secret_key_raises():
    with pytest.raises(RuntimeError) as exc:
        Settings(**_base_kwargs(secret_key="too-short")).validate_production()
    assert "SECRET_KEY" in str(exc.value)


def test_wildcard_origins_raises():
    with pytest.raises(RuntimeError) as exc:
        Settings(**_base_kwargs(allowed_origins=["*"])).validate_production()
    assert "ALLOWED_ORIGINS" in str(exc.value)


def test_clean_config_passes():
    Settings(**_base_kwargs()).validate_production()  # must not raise


def test_placeholder_openai_warns_but_does_not_block():
    """A placeholder OpenAI key should not block startup; AI features
    just degrade. validate_production may still raise for OTHER errors,
    so we use a clean baseline."""
    cfg = Settings(**_base_kwargs(openai_api_key="sk-placeholder"))
    cfg.validate_production()  # must not raise — only logs a warning
