"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@pgbouncer:6432/memory_layer"
    )

    # ── Auth ───────────────────────────────────────────────────────────────────
    # IMPORTANT: override SECRET_KEY in .env.local / .env.production
    secret_key: str = "local-dev-secret-change-this-before-production"
    access_token_expire_hours: int = 24

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
    
    # ── CORS ───────────────────────────────────────────────────────────────────
    allowed_origins: List[str] = ["*"]

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
        """Fail fast on unsafe production settings."""
        if self.environment != "production":
            return

        errors = []
        if self.demo_mode:
            errors.append("DEMO_MODE must be false in production")
        if self.secret_key == "local-dev-secret-change-this-before-production":
            errors.append("SECRET_KEY must be set to a strong production secret")
        if not self.openai_api_key or self.openai_api_key == "sk-placeholder":
            errors.append("OPENAI_API_KEY must be set in production")
        if self.allowed_origins == ["*"] or "*" in self.allowed_origins:
            errors.append("ALLOWED_ORIGINS must not be '*' in production")
        if "localhost" in self.database_url:
            errors.append("DATABASE_URL must point at a production database")

        if errors:
            raise RuntimeError("Invalid production configuration: " + "; ".join(errors))

    class Config:
        # Reads .env first, then .env.local for local overrides
        env_file = [".env", ".env.local"]
        env_file_encoding = "utf-8"
        extra = "ignore"


# Global settings instance
settings = Settings()
