"""Async database connection and session management."""

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

current_user_id: ContextVar[Optional[str]] = ContextVar(
    "current_user_id", default=None
)

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=5,
    max_overflow=5,
    pool_timeout=30,
    pool_pre_ping=True,
    # PgBouncer uses transaction pooling - prepared statements must be disabled
    connect_args={"prepared_statement_cache_size": 0},
)


def set_current_user_id(user_id: Optional[str]):
    """Set request-local user id used by PostgreSQL RLS policies."""
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


# Create async session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db(request: Request = None) -> AsyncSession:
    """Dependency for FastAPI routes to get a database session."""
    async with async_session() as session:
        try:
            user_id = getattr(getattr(request, "state", None), "current_user_id", None)
            await set_rls_context(session, user_id)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass
