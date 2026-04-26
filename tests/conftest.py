import pytest
import asyncio
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.main import app
from app.database import Base


TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_memory_test"


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a new database session for each test."""
    session_factory = async_sessionmaker(
        test_engine, expire_on_commit=False, autoflush=False
    )

    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """Register and login a test user, return auth headers."""
    user_data = {
        "email": f"test_{asyncio.current_task().get_name()}@example.com",
        "password": "testpassword123"
    }
    await client.post("/api/v1/auth/register", json=user_data)

    response = await client.post("/api/v1/auth/login", json={
        "email": user_data["email"],
        "password": user_data["password"]
    })
    token = response.json()["access_token"]

    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def test_user(client: AsyncClient) -> dict:
    """Create a test user and return user data."""
    user_data = {
        "email": f"testuser_{asyncio.current_task().get_name()}@example.com",
        "password": "testpassword123"
    }
    await client.post("/api/v1/auth/register", json=user_data)
    return user_data