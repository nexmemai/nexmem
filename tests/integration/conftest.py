"""Conftest for integration tests.

These tests run against a real Postgres + Redis. The CI workflow's
`integration-tests` job sets:

    DEMO_MODE=false
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/nexmem_test
    REDIS_URL=redis://localhost:6379/0
    RUN_DB_TESTS=1

Before any test runs, we apply Alembic migrations to head so the schema
matches the model. We also truncate every memory table between tests
to keep them isolated.

Tests in this folder are SKIPPED unless `RUN_DB_TESTS=1` is set, so the
default `pytest tests/` invocation in the unit job stays fast.
"""

from __future__ import annotations

import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


# ── Skip the whole directory unless RUN_DB_TESTS=1 is set ───────────────────
collect_ignore_glob: list[str] = []
if os.getenv("RUN_DB_TESTS") != "1":
    pytestmark = pytest.mark.skip(
        reason="Integration tests require RUN_DB_TESTS=1 and a live Postgres + Redis."
    )


def pytest_collection_modifyitems(config, items) -> None:
    """Skip every test in this directory if RUN_DB_TESTS != 1."""
    if os.getenv("RUN_DB_TESTS") == "1":
        return
    skip_marker = pytest.mark.skip(
        reason="Integration tests require RUN_DB_TESTS=1 and a live Postgres + Redis."
    )
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(skip_marker)


# ── One-time schema setup (per session) ─────────────────────────────────────


def _run_alembic_upgrade_head() -> None:
    """Apply Alembic migrations to head, fail loudly on error.

    Phase 2 (R-H10): wraps the alembic call in
    `scripts/run_migrations.py` so the integration suite exercises
    the same race-safe entry point that the production Dockerfile /
    render.yaml use.
    """
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    cmd = [sys.executable, str(repo_root / "scripts" / "run_migrations.py")]
    result = subprocess.run(cmd, env=os.environ.copy(), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"run_migrations.py failed with exit {result.returncode}:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _apply_migrations_once() -> AsyncGenerator[None, None]:
    """Apply migrations once at the start of the integration session."""
    if os.getenv("RUN_DB_TESTS") != "1":
        yield
        return
    _run_alembic_upgrade_head()
    yield


# ── Per-test DB cleanup ─────────────────────────────────────────────────────

_USER_TABLES = (
    "engrams",
    "knowledge_edges",
    "knowledge_nodes",
    "procedural_memory",
    "semantic_memory",
    "episodic_memory",
    "token_usage",
    "api_keys",
    "users",
)


@pytest_asyncio.fixture(autouse=True)
async def _truncate_between_tests() -> AsyncGenerator[None, None]:
    """Wipe all per-tenant tables between tests so order is irrelevant."""
    if os.getenv("RUN_DB_TESTS") != "1":
        yield
        return

    from app.database import engine

    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE TABLE " + ", ".join(_USER_TABLES) + " RESTART IDENTITY CASCADE")
        )
    yield


# ── HTTP client + Redis flush ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def http_client() -> AsyncGenerator[AsyncClient, None]:
    """An ASGI HTTP client bound to the app under integration env."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def redis_flushed() -> AsyncGenerator[None, None]:
    """Flush the configured Redis DB before the test."""
    if os.getenv("RUN_DB_TESTS") != "1":
        yield
        return
    import redis.asyncio as redis_async

    from app.config import settings

    if not settings.redis_url:
        yield
        return
    client = redis_async.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.flushdb()
    finally:
        await client.aclose()
    yield


# ── User helpers ────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def fresh_user(http_client: AsyncClient) -> dict:
    """Register a fresh user and return {email, password, user_id, token}."""
    email = f"int_{uuid.uuid4().hex[:8]}@example.com"
    password = "IntegrationPass!2026"
    reg = await http_client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert reg.status_code in (200, 201), f"register failed: {reg.text}"
    user_id = reg.json()["id"]

    login = await http_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert login.status_code == 200, f"login failed: {login.text}"
    token = login.json()["access_token"]

    return {
        "email": email,
        "password": password,
        "user_id": user_id,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }
