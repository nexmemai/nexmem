"""Application configuration using pydantic-settings.

Production safety contract (enforced by `validate_production`):
- DEMO_MODE must be false.
- SECRET_KEY must not be the published default and must be >= 32 chars.
- ALLOWED_ORIGINS must NOT be ["*"] (the wildcard) — explicit origins required.
- DATABASE_URL must be a real Postgres URL (the validator below already ensures
  the value is non-empty; production additionally rejects placeholder hosts).

These checks **raise** in production. They previously only logged warnings,
which meant a misconfigured deploy started up and served traffic with auth
disabled / forgeable JWTs / wildcard CORS. The hard failure is intentional.
"""

import logging
import re
from typing import List, Optional, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings


_DEFAULT_SECRET_KEY = "local-dev-secret-change-this-before-production"
_KNOWN_WEAK_SECRETS = {
    _DEFAULT_SECRET_KEY,
    "changeme_in_production",
    "changeme",
    "secret",
}
_PRODUCTION_ENV_NAMES = {"production", "prod"}
# Hosts that indicate an unconfigured / placeholder DB. Used only in
# production validation.
_PLACEHOLDER_DB_HOSTS = {"localhost", "127.0.0.1", "::1", "placeholder"}


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # ── Mode ───────────────────────────────────────────────────────────────────
    demo_mode: bool = True

    # ── Database ───────────────────────────────────────────────────────────────
    # Must be set via DATABASE_URL environment variable — no hardcoded default.
    database_url: str = ""

    @field_validator("database_url", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: Optional[str]) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(
                "DATABASE_URL is not set. Add it to Render env vars or your .env file."
            )
        v = v.strip()
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg does NOT accept sslmode or ssl in the query string.
        # SSL is handled via connect_args={"ssl": True} in database.py.
        v = re.sub(r"[?&]ssl(?:mode)?=[^&]*", "", v)
        v = v.rstrip("?&")
        return v

    # ── Auth ───────────────────────────────────────────────────────────────────
    # IMPORTANT: Set a strong random value in production env vars.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    secret_key: str = _DEFAULT_SECRET_KEY
    access_token_expire_hours: int = 4

    # ── OpenAI ─────────────────────────────────────────────────────────────────
    openai_api_key: str = "sk-placeholder"
    openai_llm_model: str = "gpt-4o"

    # ── Embedding Settings ───────────────────────────────────────────────────────
    embedding_model: str = "all-MiniLM-L6-v2"
    vector_dim: int = 384

    # ── Consolidation Settings ─────────────────────────────────────
    consolidation_interval_minutes: int = 30
    consolidation_llm_model: str = "gpt-4o-mini"

    # ── App Settings ───────────────────────────────────────────────────────────
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False
    environment: str = "development"    # "development" | "production"

    # ── Observability ──────────────────────────────────────────────────────────
    sentry_dsn: Optional[str] = None
    metrics_secret_key: Optional[str] = None

    # ── CORS ───────────────────────────────────────────────────────────────────
    allowed_origins: Union[str, List[str]] = ["*"]

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v):
        """Accept JSON list, comma-separated string, or raw string from env vars."""
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return ["*"]
            if v.startswith("[") and v.endswith("]"):
                try:
                    import json
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    pass
            v = v.replace("[", "").replace("]", "").replace("\"", "").replace("'", "")
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # ── Frontend ───────────────────────────────────────────────────────────────
    frontend_api_url: str = "http://localhost:8000"

    # ── Redis (rate limiting and quotas) ───────────────────
    redis_url: Optional[str] = None

    # ── User Tiers (quotas) ───────────────────────────────
    free_monthly_writes: int = 1000
    starter_monthly_writes: int = 10000
    pro_monthly_writes: int = 100000
    enterprise_monthly_writes: int = 1000000

    # ── Helpers ────────────────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return (self.environment or "").strip().lower() in _PRODUCTION_ENV_NAMES

    def validate_production(self) -> None:
        """Strict validation for production mode.

        RAISES RuntimeError on any unsafe config. Do not downgrade to a warning.
        """
        if not self.is_production:
            # In dev/test the validator is a no-op. This is intentional so unit
            # tests can construct Settings() without choosing a production-grade
            # secret. CORS still has a runtime guard (see app.main).
            return

        errors: list[str] = []

        # 1. DEMO_MODE — total auth bypass; never permitted in production.
        if self.demo_mode:
            errors.append(
                "DEMO_MODE is true in production. Demo mode bypasses authentication "
                "entirely (returns a synthetic user for every request) and must "
                "never run in production. Set DEMO_MODE=false."
            )

        # 2. SECRET_KEY — JWTs use HS256, so a weak/default secret means every
        #    token is forgeable.
        if (
            self.secret_key in _KNOWN_WEAK_SECRETS
            or self.secret_key.startswith("local-dev")
            or self.secret_key.startswith("test-")
            or len(self.secret_key) < 32
        ):
            errors.append(
                "SECRET_KEY is missing, the published default, or shorter than "
                "32 characters. Generate a strong value with: "
                "python -c \"import secrets; print(secrets.token_hex(32))\" "
                "and set it via your deployment provider's secret store."
            )

        # 3. ALLOWED_ORIGINS — wildcard with credentials = browser CORS chaos
        #    plus non-browser clients can be tricked. Production must enumerate.
        origins = self.allowed_origins if isinstance(self.allowed_origins, list) else [self.allowed_origins]
        if not origins or "*" in origins:
            errors.append(
                "ALLOWED_ORIGINS is unset or contains '*'. Production deployments "
                "must enumerate explicit origin URLs (e.g. https://nexmem.ai)."
            )

        # 4. DATABASE_URL — refuse to start against placeholder / loopback hosts
        #    in production. asyncpg URLs are like postgresql+asyncpg://u:p@host:port/db.
        try:
            host = self.database_url.split("@", 1)[1].split("/", 1)[0].split(":")[0]
            if host in _PLACEHOLDER_DB_HOSTS:
                errors.append(
                    f"DATABASE_URL points at the placeholder/loopback host '{host}'. "
                    "Set DATABASE_URL to a real production database."
                )
        except (IndexError, ValueError):
            errors.append("DATABASE_URL is not a recognisable Postgres connection URL.")

        # 5. OPENAI_API_KEY — informational. We do not hard-fail on this because
        #    a deployment can legitimately disable LLM-dependent features. The
        #    health endpoint surfaces the unconfigured state.
        if not self.openai_api_key or self.openai_api_key == "sk-placeholder":
            logging.getLogger(__name__).warning(
                "OPENAI_API_KEY is missing or placeholder; LLM features will be disabled."
            )

        if errors:
            joined = "\n  - " + "\n  - ".join(errors)
            raise RuntimeError(
                "Refusing to start in production with unsafe configuration:" + joined
            )

    class Config:
        env_file = [".env", ".env.local"]
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
