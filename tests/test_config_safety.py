"""Production-safety tests for app.config.Settings.validate_production().

Each test constructs a Settings() with an explicit unsafe production combo
and asserts that validate_production() raises RuntimeError with a clear
message. These guard against the regressions that previously downgraded
the production-safety raises into log warnings.
"""

import pytest

from app.config import Settings


_GOOD_SECRET = "x" * 64  # 32 bytes hex would be 64 chars; this is fine
_GOOD_DB = "postgresql+asyncpg://u:p@db.example.com:5432/main"
_GOOD_ORIGINS = ["https://nexmem.ai"]


def _safe_prod_settings(**overrides):
    base = dict(
        environment="production",
        demo_mode=False,
        secret_key=_GOOD_SECRET,
        allowed_origins=list(_GOOD_ORIGINS),
        database_url=_GOOD_DB,
        openai_api_key="sk-real-key",
    )
    base.update(overrides)
    return Settings(**base)


# ── Sanity: a fully-configured production Settings should validate cleanly ───

def test_safe_production_settings_validate_ok() -> None:
    """Baseline: when everything is set correctly, validate_production() is a no-op."""
    settings = _safe_prod_settings()
    assert settings.is_production is True
    settings.validate_production()  # must not raise


def test_dev_environment_skips_all_checks() -> None:
    """In non-production, validate_production() never raises even with weak config."""
    settings = Settings(
        environment="development",
        demo_mode=True,
        secret_key="local-dev-secret-change-this-before-production",
        allowed_origins=["*"],
        database_url="postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/placeholder",
        openai_api_key="sk-placeholder",
    )
    assert settings.is_production is False
    settings.validate_production()  # must not raise


# ── R-C2: DEMO_MODE in production must hard-fail ─────────────────────────────

def test_demo_mode_blocks_production_startup() -> None:
    settings = _safe_prod_settings(demo_mode=True)
    with pytest.raises(RuntimeError, match="DEMO_MODE is true in production"):
        settings.validate_production()


# ── R-C3: weak SECRET_KEY in production must hard-fail ───────────────────────

def test_default_secret_blocks_production_startup() -> None:
    settings = _safe_prod_settings(
        secret_key="local-dev-secret-change-this-before-production"
    )
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        settings.validate_production()


def test_short_secret_blocks_production_startup() -> None:
    settings = _safe_prod_settings(secret_key="short")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        settings.validate_production()


def test_changeme_secret_blocks_production_startup() -> None:
    settings = _safe_prod_settings(secret_key="changeme_in_production")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        settings.validate_production()


def test_test_prefixed_secret_blocks_production_startup() -> None:
    """Secrets starting with 'test-' are clearly CI/test fixtures.

    The current pytest.ini sets SECRET_KEY=test-secret-key-..., so we must
    detect that pattern in production.
    """
    settings = _safe_prod_settings(secret_key="test-this-is-a-very-long-but-clearly-test-secret")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        settings.validate_production()


# ── R-C4: wildcard CORS in production must hard-fail ─────────────────────────

def test_wildcard_origin_blocks_production_startup() -> None:
    settings = _safe_prod_settings(allowed_origins=["*"])
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        settings.validate_production()


def test_empty_origins_blocks_production_startup() -> None:
    settings = _safe_prod_settings(allowed_origins=[])
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        settings.validate_production()


# ── DB URL guards ───────────────────────────────────────────────────────────

def test_localhost_database_blocks_production_startup() -> None:
    settings = _safe_prod_settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/main"
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        settings.validate_production()


def test_loopback_ipv4_database_blocks_production_startup() -> None:
    settings = _safe_prod_settings(
        database_url="postgresql+asyncpg://u:p@127.0.0.1:5432/main"
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        settings.validate_production()


def test_placeholder_host_database_blocks_production_startup() -> None:
    settings = _safe_prod_settings(
        database_url="postgresql+asyncpg://placeholder:placeholder@placeholder:1/placeholder"
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        settings.validate_production()


# ── Combined errors are reported together ───────────────────────────────────

def test_multiple_failures_are_all_reported() -> None:
    """When several checks fail, the error message should list all of them."""
    settings = _safe_prod_settings(
        demo_mode=True,
        secret_key="bad",
        allowed_origins=["*"],
    )
    with pytest.raises(RuntimeError) as exc_info:
        settings.validate_production()

    msg = str(exc_info.value)
    assert "DEMO_MODE" in msg
    assert "SECRET_KEY" in msg
    assert "ALLOWED_ORIGINS" in msg
