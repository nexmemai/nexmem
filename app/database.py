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


def _build_engine_kwargs() -> dict:
    """Engine kwargs depend on whether we are in demo mode.

    In demo mode the engine is never used to open a connection, but the
    placeholder URL points at localhost. We disable pre-ping and SSL so
    importing the module does not attempt any network I/O.
    """
    common = dict(
        echo=settings.debug,
        pool_size=5,
        max_overflow=5,
        pool_timeout=30,
        pool_pre_ping=not settings.demo_mode,
    )
    connect_args: dict = {
        # PgBouncer transaction pooling — disable prepared-statement cache.
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0,
    }
    if not settings.demo_mode:
        connect_args["ssl"] = "require"
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


async def set_rls_context(session: AsyncSession, user_id: Optional[str]) -> None:
    """Apply the RLS user id to the current database transaction."""
    if not user_id:
        return
    await session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
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


async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db(request: Request = None) -> AsyncSession:
    """Dependency for FastAPI routes to get a database session.

    Honors the per-request user id set by the HTTP middleware so RLS
    policies see the right identity for every query. In demo mode the
    session is created but never connects (every router branches on
    ``settings.demo_mode`` before issuing SQL), so we skip the RLS
    SELECT that would otherwise try to talk to the placeholder URL.
    """
    async with async_session() as session:
        try:
            if not settings.demo_mode:
                user_id = getattr(
                    getattr(request, "state", None), "current_user_id", None
                )
                await set_rls_context(session, user_id)
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
