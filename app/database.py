"""Async database connection and session management.

Phase 2 changes:
* The engine binds to ``settings.effective_database_url`` so that demo
  mode does not require a real DATABASE_URL. The placeholder URL never
  opens a connection because every router branches on
  ``settings.demo_mode`` before touching the engine.
* SSL is only requested when the engine targets a real Postgres URL,
  so the placeholder URL does not raise on an unreachable host.
* RLS context is owned by the HTTP middleware (see app/main.py). The
  helpers here apply it to the active SQLAlchemy session/transaction.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import settings


# Per-request user id used by Postgres RLS policies.
current_user_id: ContextVar[Optional[str]] = ContextVar(
    "current_user_id", default=None
)

# Per-request app id used by Phase 4 (P4-B4) app-level RLS policies on
# the 5 memory tables (migration 019). The companion contextvar to
# ``current_user_id``. Not all auth paths populate it:
#   * API-key auth populates it from ``api_keys.app_id`` if non-NULL.
#   * Bearer JWT auth currently leaves it None (tokens do not carry
#     app_id; binding a JWT to an app is a future change).
#   * Celery tasks default it to None — single-user-scope writes still
#     work because the migration-019 policy treats NULL ``app_id`` rows
#     as visible to any current_app_id (including NULL).
# The set_config translation is "" -> NULL via the
# ``NULLIF(current_setting(..., true), '')::uuid`` wrapper used by
# every relevant policy.
current_app_id: ContextVar[Optional[str]] = ContextVar(
    "current_app_id", default=None
)


def _build_engine_kwargs() -> dict:
    """Engine kwargs depend on whether we are in demo mode.

    In demo mode the engine is never used to open a connection, but the
    placeholder URL points at localhost. We disable pre-ping and SSL so
    importing the module does not attempt any network I/O.

    Phase 5 (P5-C1): every non-demo connection sets
    ``statement_timeout`` and ``idle_in_transaction_session_timeout``
    via asyncpg's ``server_settings`` so a runaway query cannot pin a
    pooler connection forever.

    Phase 8 (P5-C2): pool sizing is sourced from settings so an
    operator can tune via env vars (``DB_POOL_SIZE``, ``DB_MAX_OVERFLOW``,
    ``DB_POOL_TIMEOUT``, ``DB_POOL_RECYCLE``) without redeploying
    code. Default math:
        Render free tier: 1 worker, Supabase free: 20 max connections.
        Pool size 5 + max overflow 10 = 15 max per worker.
        Leave 5 for admin/migrations. Total = 20. Safe.
    Bump these together with ``db_max_overflow`` and ``replicas`` when
    moving to Supabase Pro (max_client_conn 200) so the math stays
    below the upstream limit.
    """
    common = dict(
        echo=settings.debug,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=not settings.demo_mode,
    )
    connect_args: dict = {
        # PgBouncer transaction pooling — disable prepared-statement cache.
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0,
    }
    if not settings.demo_mode and settings.db_require_ssl:
        connect_args["ssl"] = "require"
        # P5-C1: kill any statement that runs longer than the configured
        # timeout (30s default), and any transaction that goes idle for
        # longer than ``idle_in_transaction_session_timeout`` (60s default).
        # asyncpg accepts these via ``server_settings`` and applies them
        # on every new connection. Values are stringified milliseconds —
        # PostgreSQL parses them as ``<n>ms``.
        connect_args["server_settings"] = {
            "statement_timeout": f"{settings.db_statement_timeout_ms}ms",
            "idle_in_transaction_session_timeout": (
                f"{settings.db_idle_in_transaction_timeout_ms}ms"
            ),
            # ``application_name`` makes pg_stat_activity readable so the
            # operator can identify which service holds a long-running
            # query during an incident.
            "application_name": f"nexmem-{settings.environment}",
        }
    common["connect_args"] = connect_args
    return common


engine = create_async_engine(
    settings.effective_database_url,
    **_build_engine_kwargs(),
)


def set_current_user_id(user_id: Optional[str]):
    """Set request-local user id used by PostgreSQL RLS policies.

    Returns the contextvar token so the caller can reset it later.
    """
    return current_user_id.set(str(user_id) if user_id else None)


def reset_current_user_id(token) -> None:
    """Reset request-local user id context."""
    current_user_id.reset(token)


def set_current_app_id(app_id: Optional[str]):
    """Set request-local app id used by Phase 4 (P4-B4) RLS policies.

    Mirrors ``set_current_user_id``. Returns the contextvar token so
    the caller can reset it later.
    """
    return current_app_id.set(str(app_id) if app_id else None)


def reset_current_app_id(token) -> None:
    """Reset request-local app id context."""
    current_app_id.reset(token)


async def set_rls_context(
    session: AsyncSession,
    user_id: Optional[str],
    app_id: Optional[str] = None,
) -> None:
    """Apply RLS context to the current database transaction.

    Sets ``app.current_user_id`` and ``app.current_app_id`` so the
    migration-008 / 013 / 019 policies see the right identity for this
    request. Empty string (rather than NULL) is intentional: the
    policies wrap each setting in ``NULLIF(current_setting(...), '')``
    which converts "" to NULL — that yields the documented
    "NULL app_id rows are visible regardless of current_app_id" behaviour.
    """
    if not user_id:
        return
    await session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )
    # Phase 4 (P4-B4) — wire app.current_app_id immediately after
    # current_user_id, every time. Empty string is the documented
    # "NULL app_id" sentinel for the migration-019 policy.
    await session.execute(
        text("SELECT set_config('app.current_app_id', :aid, true)"),
        {"aid": str(app_id) if app_id else ""},
    )


@event.listens_for(Session, "after_begin")
def set_rls_context_on_begin(session, transaction, connection) -> None:
    """Apply request-local RLS context to every PostgreSQL transaction."""
    user_id = current_user_id.get()
    if not user_id or connection.dialect.name != "postgresql":
        return
    connection.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )
    # Phase 4 (P4-B4) — wire app.current_app_id immediately after
    # current_user_id. The contextvar may be None for JWT auth and
    # Celery tasks; "" sentinel resolves to NULL via NULLIF in policy.
    app_id = current_app_id.get()
    connection.execute(
        text("SELECT set_config('app.current_app_id', :aid, true)"),
        {"aid": str(app_id) if app_id else ""},
    )


async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db(request: Request = None) -> AsyncSession:
    """Dependency for FastAPI routes to get a database session.

    Honors the per-request user id and app id set by the HTTP
    middleware so RLS policies see the right identity for every query.
    In demo mode the session is created but never connects (every
    router branches on ``settings.demo_mode`` before issuing SQL), so
    we skip the RLS SELECT that would otherwise try to talk to the
    placeholder URL.
    """
    async with async_session() as session:
        try:
            if not settings.demo_mode:
                req_state = getattr(request, "state", None)
                user_id = getattr(req_state, "current_user_id", None)
                app_id = getattr(req_state, "current_app_id", None)
                await set_rls_context(session, user_id, app_id)
            yield session
            if not settings.demo_mode:
                await session.commit()
        except Exception:
            if not settings.demo_mode:
                await session.rollback()
            raise
        finally:
            await session.close()


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass
