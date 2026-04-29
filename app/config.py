"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/memory_layer"
    )

    # ── Auth ───────────────────────────────────────────────────────────────────
    # IMPORTANT: override SECRET_KEY in .env.local / .env.production
    secret_key: str = "local-dev-secret-change-this-before-production"
    access_token_expire_hours: int = 24

    # ── OpenAI ─────────────────────────────────────────────────────────────────
    openai_api_key: str = "sk-placeholder"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_llm_model: str = "gpt-4o"

    # ── Memory Settings ────────────────────────────────────────────────────────
    memory_decay_days: int = 30
    semantic_top_k: int = 5
    vector_dim: int = 1536

    # ── Consolidation Settings ─────────────────────────────────────
    consolidation_interval_minutes: int = 30
    consolidation_llm_model: str = "gpt-4o-mini"

    # ── App Settings ───────────────────────────────────────────────────────────
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False
    environment: str = "development"    # "development" | "production"

    # ── CORS ───────────────────────────────────────────────────────────────────
    allowed_origins: List[str] = ["*"]

    # ── Frontend ───────────────────────────────────────────────────────────────
    frontend_api_url: str = "http://localhost:8000"

    # ── Demo mode (in-memory storage, no PostgreSQL required) ─────────────────
    demo_mode: bool = True

    class Config:
        # Reads .env first, then .env.local for local overrides
        env_file = [".env", ".env.local"]
        env_file_encoding = "utf-8"
        extra = "ignore"


# Global settings instance
settings = Settings()
