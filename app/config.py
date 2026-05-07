"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings
from typing import List, Optional

from pydantic import field_validator


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
        # Normalise scheme to asyncpg
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg does NOT accept sslmode or ssl in the query string.
        # SSL is handled via connect_args={"ssl": True} in database.py.
        import re
        v = re.sub(r"[?&]ssl(?:mode)?=[^&]*", "", v)
        # Clean up any leftover trailing ? or &
        v = v.rstrip("?&")
        return v

    # ── Auth ───────────────────────────────────────────────────────────────────
    # IMPORTANT: Set a strong random value in Render env vars.
    # Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
    secret_key: str = "local-dev-secret-change-this-before-production"
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
    # Set METRICS_SECRET_KEY in Render to enable the /metrics endpoint.
    # Use: Authorization: Bearer <METRICS_SECRET_KEY>
    metrics_secret_key: Optional[str] = None
    
    # ── CORS ───────────────────────────────────────────────────────────────────
    allowed_origins: List[str] = ["*"]

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
            
            # Try parsing as JSON first (to handle ["a", "b"] format)
            if v.startswith("[") and v.endswith("]"):
                try:
                    import json
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    pass # Fallback to manual cleaning

            # Manual cleaning: remove brackets and quotes
            v = v.replace("[", "").replace("]", "").replace("\"", "").replace("'", "")
            
            # Handle comma-separated: "https://a.com, https://b.com"
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # ── Frontend ───────────────────────────────────────────────────────────────
    frontend_api_url: str = "http://localhost:8000"

    # ── Redis (for rate limiting and quotas) ───────────────────
    redis_url: Optional[str] = None

    # ── User Tiers (quotas) ───────────────────────────────
    free_monthly_writes: int = 1000
    starter_monthly_writes: int = 10000
    pro_monthly_writes: int = 100000
    enterprise_monthly_writes: int = 1000000

    def validate_production(self) -> None:
        """Strict validation for production mode — raises on insecure config."""
        if self.demo_mode:
            return

        errors = []

        if (
            self.secret_key.startswith("local-dev")
            or self.secret_key == "changeme_in_production"
            or len(self.secret_key) < 32
        ):
            errors.append(
                "SECRET_KEY is too weak or is the default value. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

        if not self.openai_api_key or self.openai_api_key == "sk-placeholder":
            import logging
            logging.getLogger(__name__).warning(
                "OPENAI_API_KEY is missing or placeholder — AI summarisation features disabled."
            )

        if self.allowed_origins == ["*"]:
            import logging
            logging.getLogger(__name__).warning(
                "ALLOWED_ORIGINS is '*' which allows any origin. "
                "Set it to your frontend domain(s) in Render env vars."
            )

        if errors:
            raise RuntimeError("Invalid production configuration:\n  - " + "\n  - ".join(errors))

    class Config:
        # Reads .env first, then .env.local for local overrides
        env_file = [".env", ".env.local"]
        env_file_encoding = "utf-8"
        extra = "ignore"


# Global settings instance
settings = Settings()
