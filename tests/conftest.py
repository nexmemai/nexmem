"""
Shared test fixtures for the entire Nexmem test suite.

Strategy: run every test against the app in DEMO_MODE=true.
This uses the existing in-memory stores (demo_db.py) as a database substitute,
so the full test suite runs without a live PostgreSQL instance.
"""
import os
import pytest
import asyncio

# ── Force demo mode before the app module is ever imported ───────────────────
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci-only")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-placeholder")

from typing import AsyncGenerator, Generator
from httpx import AsyncClient, ASGITransport


# ── Import AFTER env vars are set ────────────────────────────────────────────
from app.main import app
from app import demo_db


# ── Event loop ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Single event loop for the entire test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# ── HTTP client ───────────────────────────────────────────────────────────────
@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client wired to the demo-mode FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


# ── Demo-store reset between tests ───────────────────────────────────────────
@pytest.fixture(autouse=True)
def reset_demo_stores():
    """
    Clear all in-memory demo stores before each test so that tests are fully
    isolated from one another.
    """
    demo_db.episodic_store.clear()
    demo_db.semantic_store.clear()
    demo_db.procedural_store.clear()
    demo_db.graph_nodes_store.clear()
    demo_db.graph_edges_store.clear()
    demo_db.reset_demo_auth()
    yield
    # Optionally clear again on teardown
    demo_db.episodic_store.clear()
    demo_db.semantic_store.clear()
    demo_db.procedural_store.clear()
    demo_db.graph_nodes_store.clear()
    demo_db.graph_edges_store.clear()
    demo_db.reset_demo_auth()


# ── Auth helper ───────────────────────────────────────────────────────────────
@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """
    Register a unique user and return a valid Bearer-token auth header.
    Works entirely through the demo-mode auth flow (no DB needed).
    """
    import uuid
    unique = uuid.uuid4().hex[:8]
    creds = {"email": f"test_{unique}@nexmem.example.com", "password": "TestPass123!"}

    reg = await client.post("/api/v1/auth/register", json=creds)
    assert reg.status_code in (200, 201), f"Registration failed: {reg.text}"

    login = await client.post("/api/v1/auth/login", json=creds)
    assert login.status_code == 200, f"Login failed: {login.text}"

    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def demo_user_id(client: AsyncClient, auth_headers: dict) -> str:
    """Return the user_id of the currently authenticated demo user."""
    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert me.status_code == 200
    return me.json()["id"]