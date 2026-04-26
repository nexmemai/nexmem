"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/memory_layer"

    # OpenAI
    openai_api_key: str = "sk-placeholder"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_llm_model: str = "gpt-4o"

    # Memory Settings
    memory_decay_days: int = 30
    semantic_top_k: int = 5
    vector_dim: int = 1536

    # App Settings
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False

    # Frontend
    frontend_api_url: str = "http://localhost:8000"

    # Demo mode (uses in-memory storage instead of PostgreSQL)
    demo_mode: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# Global settings instance
settings = Settings()
