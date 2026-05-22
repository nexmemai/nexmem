"""Top-level test fixtures.

Strategy:
  - The default test environment is `DEMO_MODE=true`. Most tests at this
    level are unit / demo-mode tests that use the in-memory stores in
    `app.demo_db` instead of a database.
  - Tests under `tests/integration/` opt out of demo mode by setting
    `DEMO_MODE=false` (along with `RUN_DB_TESTS=1`) before they import the
    app, and they have their own conftest that boots a real DB session.
  - We do NOT force `DEMO_MODE=true` here unless the env var is unset,
    so an integration run that explicitly sets `DEMO_MODE=false` is
    respected.
"""

import asyncio
import os
import uuid
from typing import AsyncGenerator, Generator

import pytest

# Set demo-mode + safe placeholders BEFORE importing the app, but only when
# they are not already set by the test environment (e.g., by the integration
# CI job).
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci-only")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-placeholder")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/placeholder",
)
os.environ.setdefault("ENVIRONMENT", "development")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app import demo_db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


# ── Event loop ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Single event loop for the entire test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# ── HTTP client ──────────────────────────────────────────────────────────────
@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client wired to the FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


# ── Demo-store reset between tests ──────────────────────────────────────────
@pytest.fixture(autouse=True)
def reset_demo_stores():
    """Clear in-memory demo stores between tests (no-op when not in demo mode)."""
    if not settings.demo_mode:
        yield
        return

    demo_db.episodic_store.clear()
    demo_db.semantic_store.clear()
    demo_db.procedural_store.clear()
    demo_db.graph_nodes_store.clear()
    demo_db.graph_edges_store.clear()
    yield
    demo_db.episodic_store.clear()
    demo_db.semantic_store.clear()
    demo_db.procedural_store.clear()
    demo_db.graph_nodes_store.clear()
    demo_db.graph_edges_store.clear()


# ── Auth helper ──────────────────────────────────────────────────────────────
@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """Register a unique user and return a Bearer-token auth header.

    Uses `@example.com` (an IANA example TLD) instead of `.test` because
    pydantic's email-validator rejects `.test` as a special-use TLD.
    """
    unique = uuid.uuid4().hex[:8]
    creds = {"email": f"test_{unique}@example.com", "password": "TestPass123!"}

    reg = await client.post("/api/v1/auth/register", json=creds)
    assert reg.status_code in (200, 201), f"Registration failed: {reg.text}"

    login = await client.post("/api/v1/auth/login", json=creds)
    assert login.status_code == 200, f"Login failed: {login.text}"

    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def demo_user_id(client: AsyncClient, auth_headers: dict) -> str:
    """Return the user_id of the currently authenticated user."""
    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert me.status_code == 200
    return me.json()["id"]
