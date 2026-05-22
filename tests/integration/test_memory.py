import pytest
import os
from httpx import AsyncClient

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="requires live PostgreSQL/Supabase database; set RUN_DB_TESTS=1",
)


@pytest.mark.asyncio
async def test_health_live(client: AsyncClient):
    """Test GET /health/live returns 200."""
    response = await client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    """Test GET / returns service info."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Decentralized AI Memory Layer"
    assert data["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_register_user(client: AsyncClient):
    """Test user registration works."""
    response = await client.post("/api/v1/auth/register", json={
        "email": "newuser@example.com",
        "password": "securepassword123"
    })
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "newuser@example.com"
    assert data["is_active"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    """Test registering with duplicate email fails."""
    user_data = {"email": "duplicate@example.com", "password": "password123"}
    await client.post("/api/v1/auth/register", json=user_data)

    response = await client.post("/api/v1/auth/register", json=user_data)
    assert response.status_code == 400
    assert "already registered" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    """Test login with valid credentials returns token."""
    await client.post("/api/v1/auth/register", json={
        "email": "logintest@example.com",
        "password": "password123"
    })

    response = await client.post("/api/v1/auth/login", json={
        "email": "logintest@example.com",
        "password": "password123"
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_credentials(client: AsyncClient):
    """Test login with invalid credentials fails."""
    response = await client.post("/api/v1/auth/login", json={
        "email": "nonexistent@example.com",
        "password": "wrongpassword"
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_without_auth(client: AsyncClient):
    """Test accessing protected endpoint without auth returns 401."""
    response = await client.post("/api/v1/auth/api-keys", json={
        "name": "Test Key"
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_api_key(auth_headers: dict, client: AsyncClient):
    """Test creating an API key returns raw key once."""
    response = await client.post(
        "/api/v1/auth/api-keys",
        json={"name": "Test CLI Key"},
        headers=auth_headers
    )
    assert response.status_code == 201
    data = response.json()
    assert "api_key" in data
    assert data["api_key"].startswith("mem_")
    assert data["name"] == "Test CLI Key"


@pytest.mark.asyncio
async def test_list_api_keys(auth_headers: dict, client: AsyncClient):
    """Test listing API keys works."""
    await client.post(
        "/api/v1/auth/api-keys",
        json={"name": "Key 1"},
        headers=auth_headers
    )

    response = await client.get("/api/v1/auth/api-keys", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert "api_key" not in data[0]


@pytest.mark.asyncio
async def test_delete_api_key(auth_headers: dict, client: AsyncClient):
    """Test deleting an API key works."""
    create_response = await client.post(
        "/api/v1/auth/api-keys",
        json={"name": "Key to Delete"},
        headers=auth_headers
    )
    key_id = create_response.json()["id"]

    response = await client.delete(
        f"/api/v1/auth/api-keys/{key_id}",
        headers=auth_headers
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_get_current_user_info(auth_headers: dict, client: AsyncClient):
    """Test getting current user info works."""
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert "email" in data


@pytest.mark.asyncio
async def test_user_isolation(auth_headers: dict, client: AsyncClient):
    """Test that user A cannot see user B's data."""
    user_a_key_response = await client.post(
        "/api/v1/auth/api-keys",
        json={"name": "User A Key"},
        headers=auth_headers
    )
    user_a_key = user_a_key_response.json()["api_key"]

    new_client = AsyncClient(base_url="http://test")
    await new_client.post("/api/v1/auth/register", json={
        "email": "user_b@example.com",
        "password": "password123"
    })
    login_response = await new_client.post("/api/v1/auth/login", json={
        "email": "user_b@example.com",
        "password": "password123"
    })
    user_b_token = login_response.json()["access_token"]
    user_b_headers = {"Authorization": f"Bearer {user_b_token}"}

    user_b_keys = await new_client.get("/api/v1/auth/api-keys", headers=user_b_headers)
    assert user_b_keys.status_code == 200
    key_names = [k["name"] for k in user_b_keys.json()]
    assert "User A Key" not in key_names
