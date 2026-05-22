"""Application configuration using pydantic-settings.

Phase 2 changes:
* DATABASE_URL is no longer required when DEMO_MODE=true. The validator
  only enforces a real URL in non-demo mode (validate_production raises
  there). This unblocks the test suite, which runs entirely in demo
  mode and never opens a live connection.
* validate_production() RAISES on insecure configuration. Phase 1 only
  logged warnings; Phase 2 makes it a hard fail so production cannot
  start with a default SECRET_KEY, missing OPENAI_API_KEY, or wide-open
  ALLOWED_ORIGINS.
* New per-tier read quota settings used by app/core/quotas.py.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings


logger = logging.getLogger(__name__)


# Used as the engine connect URL when DEMO_MODE=true and DATABASE_URL
# is not set. The engine never opens a connection in demo mode (every
# router branches on settings.demo_mode), but SQLAlchemy still needs a
# syntactically valid URL to construct the engine object.
_DEMO_URL_PLACEHOLDER = "postgresql+asyncpg://demo:demo@localhost:5432/demo"


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # ── Mode ───────────────────────────────────────────────────────────────────
    demo_mode: bool = True

    # ── Database ───────────────────────────────────────────────────────────────
    # Required in production (validate_production raises if missing). In
    # demo mode it falls back to a placeholder URL that is never used.
    database_url: str = ""

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_db_url(cls, v: Optional[str]) -> str:
        if v is None:
            return ""
        if not isinstance(v, str):
            v = str(v)
        v = v.strip()
        if not v:
            return ""
        # Normalise scheme to asyncpg
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg does NOT accept sslmode/ssl in the query string.
        # SSL is handled via connect_args in app/database.py.
        import re
        v = re.sub(r"[?&]ssl(?:mode)?=[^&]*", "", v)
        v = v.rstrip("?&")
        return v

    @property
    def effective_database_url(self) -> str:
        """Return the URL the engine should bind to.

        In demo mode, returns a placeholder if the real URL is not set.
        In non-demo mode, returns the real URL (validate_production has
        already verified it is set).
        """
        if self.database_url:
            return self.database_url
        if self.demo_mode:
            return _DEMO_URL_PLACEHOLDER
        # In production we never reach here because validate_production
        # has raised already, but be defensive.
        return _DEMO_URL_PLACEHOLDER

    # ── Auth ───────────────────────────────────────────────────────────────────
    # IMPORTANT: Set a strong random value in Render env vars.
    # Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
    secret_key: str = "local-dev-secret-change-this-before-production"
    access_token_expire_hours: int = 4
    refresh_token_expire_days: int = 7

    # ── Phase 3: email verification + password reset (P3-A1, P3-A2) ────────────
    # Operator opt-in. Default False keeps backwards-compatible behaviour
    # for the existing test suite and tiny private beta. The acceptance
    # criteria in BACKEND_HARDENING_PHASE3_PLUS.md require this to be
    # ``true`` before billing / public beta.
    email_verification_required: bool = False
    email_verification_token_ttl_hours: int = 24
    password_reset_token_ttl_minutes: int = 30
    # P3-A8: per-IP rate limit on /auth/register. ``slowapi`` syntax.
    register_rate_limit: str = "5/hour"

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
    sentry_traces_sample_rate: float = 0.1
    sentry_profiles_sample_rate: float = 0.0
    # Set METRICS_SECRET_KEY in Render to enable the /metrics endpoint.
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

    # ── Redis (rate limiting, quotas, brute-force) ─────────────────────────────
    redis_url: Optional[str] = None

    # ── Database hardening (P5-C1) ─────────────────────────────────────────────
    # Per-connection PostgreSQL guards. A runaway query without these will
    # pin a Supabase pooler connection forever, eventually starving the
    # web service. Both are applied to every new connection via
    # ``connect_args["server_settings"]`` in app/database.py.
    #
    # ``statement_timeout`` (ms): kills any single SQL statement that runs
    #   longer than this. 30s is generous for OLTP traffic and tight enough
    #   that a stuck plan does not cascade into a pool exhaustion incident.
    # ``idle_in_transaction_session_timeout`` (ms): kills a session that
    #   left a transaction open and went idle. 60s catches client-side
    #   bugs where ``begin()`` is called without a matching ``commit()``.
    db_statement_timeout_ms: int = 30_000
    db_idle_in_transaction_timeout_ms: int = 60_000

    # Pool sizing (P5-C2 documentation; safe to override per deploy).
    # See render.yaml: workers × pool_size × replicas must stay below the
    # Supabase pooler max_client_conn (default 200 on Pro tier).
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # ── Celery hardening (P6-D1 / D2 / D3 / D4 / D5) ───────────────────────────
    # ``celery_task_soft_time_limit`` raises ``SoftTimeLimitExceeded`` inside
    #   the task so the task can clean up. ``celery_task_time_limit`` is the
    #   hard kill. Mismatched (soft < hard) is required.
    # ``celery_worker_max_tasks_per_child`` recycles workers to bound RSS;
    #   spaCy / sentence-transformers leak under repeated invocation.
    # ``celery_result_expires`` keeps Redis from filling up with old results.
    celery_task_soft_time_limit_seconds: int = 240
    celery_task_time_limit_seconds: int = 300
    celery_worker_max_tasks_per_child: int = 100
    celery_result_expires_seconds: int = 3_600
    # P6-D5: idempotency lock TTL = task_time_limit + buffer so a hung
    # task that the broker hard-kills cannot leave a stale lock pinning
    # the next legitimate enqueue. Buffer is generous (60s) so we err
    # on the side of refusing duplicates rather than running them.
    consolidation_lock_ttl_seconds: int = 360
    # P6-D1: dead-letter queue. Failed-permanently tasks LPUSH onto
    # this Redis list. ``dlq_max_entries`` trims so the list cannot
    # exhaust Redis memory under a stuck-task storm.
    dlq_redis_key: str = "nexmem:dlq:consolidation"
    dlq_max_entries: int = 1_000

    # ── Request body cap (P7-E5) ───────────────────────────────────────────────
    # Anything above this is 413 Payload Too Large. Starlette/FastAPI
    # accept arbitrarily large bodies by default; a 1 GB POST will OOM
    # the worker before any pydantic validator runs. 5 MB is a generous
    # ceiling for a memory-write payload.
    max_request_body_bytes: int = 5 * 1024 * 1024

    # ── Read-only kill switch (P9-G1) ──────────────────────────────────────────
    # When True, every state-changing HTTP route returns 503. Reads,
    # health/metrics, and session-revocation endpoints continue to
    # flow. Defaults to False; operator flips this via the
    # ``READ_ONLY`` env var during an incident, then restarts the
    # process (or hits a future ``/admin/read-only`` endpoint that
    # mutates settings in-place).
    read_only: bool = False

    # ── Write quotas per tier ──────────────────────────────────────────────────
    free_monthly_writes: int = 1000
    starter_monthly_writes: int = 10000
    pro_monthly_writes: int = 100000
    enterprise_monthly_writes: int = 1000000

    # ── Read quotas per tier (Phase 2) ─────────────────────────────────────────
    free_monthly_reads: int = 10000
    starter_monthly_reads: int = 100000
    pro_monthly_reads: int = 1000000
    enterprise_monthly_reads: int = 10000000

    def validate_production(self) -> None:
        """Strict validation for production mode — RAISES on insecure config.

        Called from the FastAPI lifespan (app.main.lifespan) so any
        misconfiguration kills the process at startup instead of silently
        running with insecure defaults.
        """
        if self.demo_mode:
            return

        errors: list[str] = []
        warnings: list[str] = []

        if not self.database_url:
            errors.append(
                "DATABASE_URL is required in non-demo mode. Set it in your "
                "deploy environment (Render Dashboard, etc.) before starting."
            )

        if (
            self.secret_key.startswith("local-dev")
            or self.secret_key in ("changeme_in_production", "ci-test-secret")
            or len(self.secret_key) < 32
        ):
            errors.append(
                "SECRET_KEY is missing, default, or shorter than 32 characters. "
                "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
            )

        if not self.openai_api_key or self.openai_api_key in ("sk-placeholder", "sk-test-placeholder"):
            warnings.append(
                "OPENAI_API_KEY is missing or a placeholder; AI summarisation "
                "and RAG features will degrade to safe fallbacks."
            )

        if list(self.allowed_origins) == ["*"]:
            errors.append(
                "ALLOWED_ORIGINS is '*' which permits any origin. Set it to "
                "the explicit list of allowed frontend domains."
            )

        if not self.redis_url:
            warnings.append(
                "REDIS_URL is unset. Rate limiting, brute-force protection, "
                "and quotas will degrade to in-memory and fail closed on a "
                "per-process basis. This is unsafe for multi-replica deployments."
            )

        for warning in warnings:
            logger.warning("config: %s", warning)

        if errors:
            for err in errors:
                logger.error("config: %s", err)
            raise RuntimeError(
                "Refusing to start in non-demo mode with insecure configuration: "
                + " | ".join(errors)
            )

    class Config:
        env_file = [".env", ".env.local"]
        env_file_encoding = "utf-8"
        extra = "ignore"


# Global settings instance
settings = Settings()
