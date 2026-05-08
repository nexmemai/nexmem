"""Security-focused configuration tests."""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings


DATABASE_URL = "postgresql+asyncpg://user:pass@example.com:5432/app"
STRONG_SECRET = "test-secret-key-for-production-checks-32"
DEFAULT_SECRET = "local-dev-secret-change-this-before-production"


def _settings(**overrides) -> Settings:
    values = {
        "database_url": DATABASE_URL,
        "demo_mode": False,
        "environment": "production",
        "secret_key": STRONG_SECRET,
        "openai_api_key": "sk-test-placeholder",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_secret_key_is_required(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url=DATABASE_URL,
            demo_mode=False,
            environment="production",
            openai_api_key="sk-test-placeholder",
        )


def test_production_rejects_short_secret_key():
    with pytest.raises(ValidationError):
        _settings(secret_key="too-short")


def test_production_rejects_default_secret_key():
    settings = _settings(secret_key=DEFAULT_SECRET)

    with pytest.raises(RuntimeError, match="SECRET_KEY is too weak"):
        settings.validate_production()


def test_production_accepts_strong_secret_key():
    settings = _settings(secret_key=STRONG_SECRET)

    settings.validate_production()


def test_production_rejects_wildcard_allowed_origins():
    settings = _settings(allowed_origins=["*"])

    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS must explicitly list"):
        settings.validate_production()


def test_production_rejects_empty_allowed_origins():
    settings = _settings(allowed_origins="")

    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS must explicitly list"):
        settings.validate_production()


def test_production_accepts_explicit_allowed_origins():
    settings = _settings(
        allowed_origins="https://nexmem.vercel.app,https://nexmem-1.onrender.com"
    )

    assert settings.allowed_origins == [
        "https://nexmem.vercel.app",
        "https://nexmem-1.onrender.com",
    ]
    settings.validate_production()


def test_default_allowed_origins_are_local_development_only():
    settings = _settings(environment="development")

    assert "*" not in settings.allowed_origins
    assert "http://localhost:3000" in settings.allowed_origins


def test_explicit_allowed_origin_receives_cors_headers():
    settings = _settings(allowed_origins="https://nexmem.vercel.app")
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    response = TestClient(app).options(
        "/ping",
        headers={
            "Origin": "https://nexmem.vercel.app",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://nexmem.vercel.app"
    assert response.headers["access-control-allow-credentials"] == "true"
