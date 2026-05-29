"""
Task 6.1: Integration tests for the unified write flow and app_id isolation.

All tests run in DEMO_MODE=true (in-memory stores, no Postgres required).
The conftest.py `reset_demo_stores` fixture is autouse, guaranteeing a clean
state at the start of every test.
"""
import uuid
import pytest
from httpx import AsyncClient


# ──────────────────────────────────────────────────────────────────────────────
# Health & Basic Connectivity
# ──────────────────────────────────────────────────────────────────────────────

class TestHealthEndpoints:
    """Sanity checks — these must pass before any other tests are meaningful."""

    @pytest.mark.asyncio
    async def test_health_live(self, client: AsyncClient):
        r = await client.get("/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_root_returns_service_info(self, client: AsyncClient):
        r = await client.get("/")
        assert r.status_code == 200
        body = r.json()
        assert "service" in body or "mode" in body  # both valid depending on version


# ──────────────────────────────────────────────────────────────────────────────
# Authentication & Authorization
# ──────────────────────────────────────────────────────────────────────────────

class TestAuthentication:
    """Verify that the auth layer is correctly enforced in demo mode."""

    @pytest.mark.asyncio
    async def test_register_and_login(self, client: AsyncClient):
        """Full register→login flow returns a JWT."""
        uid = uuid.uuid4().hex[:6]
        creds = {"email": f"auth_{uid}@test.com", "password": "Pass!1234"}

        reg = await client.post("/api/v1/auth/register", json=creds)
        assert reg.status_code in (200, 201)

        login = await client.post("/api/v1/auth/login", json=creds)
        assert login.status_code == 200
        assert "access_token" in login.json()

    @pytest.mark.asyncio
    async def test_duplicate_email_rejected(self, client: AsyncClient):
        """Registering with the same email twice should fail."""
        creds = {"email": "dup@test.com", "password": "Pass!1234"}
        await client.post("/api/v1/auth/register", json=creds)
        r = await client.post("/api/v1/auth/register", json=creds)
        assert r.status_code in (400, 409)

    @pytest.mark.asyncio
    async def test_wrong_password_rejected(self, client: AsyncClient):
        """Login with incorrect password returns 401."""
        creds = {"email": "wrong@test.com", "password": "Pass!1234"}
        await client.post("/api/v1/auth/register", json=creds)
        r = await client.post("/api/v1/auth/login", json={"email": "wrong@test.com", "password": "BAD"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_endpoint_requires_token(self, client: AsyncClient):
        """Hitting a protected endpoint with no token returns 401/403."""
        r = await client.get("/api/v1/auth/me")
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_get_me_with_valid_token(self, client: AsyncClient, auth_headers: dict):
        """Authenticated /me endpoint returns the caller's info."""
        r = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert r.status_code == 200
        assert "id" in r.json()
        assert "email" in r.json()


# ──────────────────────────────────────────────────────────────────────────────
# Unified Write Flow (Episodic Memory)
# ──────────────────────────────────────────────────────────────────────────────

class TestUnifiedWriteFlow:
    """
    Task 6.1: Verify the core write path — memories must be created, readable,
    and properly rejected on bad input.
    """

    @pytest.mark.asyncio
    async def test_write_episodic_memory_success(
        self, client: AsyncClient, auth_headers: dict, demo_user_id: str
    ):
        """A valid episodic write returns a record with an id."""
        payload = {
            "content": "The user prefers Python over JavaScript.",
            "user_id": demo_user_id,
            "session_id": "session_001",
        }
        r = await client.post(
            f"/api/v1/agents/{demo_user_id}/episodes",
            json=payload,
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "id" in body

    @pytest.mark.asyncio
    async def test_write_without_auth_rejected(
        self, client: AsyncClient, demo_user_id: str
    ):
        """Writing without a token returns 401/403."""
        r = await client.post(
            f"/api/v1/agents/{demo_user_id}/episodes",
            json={"content": "should fail", "user_id": demo_user_id, "session_id": "s"},
        )
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_written_memory_is_readable(
        self, client: AsyncClient, auth_headers: dict, demo_user_id: str
    ):
        """A memory written via POST must appear in a subsequent GET."""
        unique_content = f"unique_content_{uuid.uuid4().hex}"
        await client.post(
            f"/api/v1/agents/{demo_user_id}/episodes",
            json={"content": unique_content, "user_id": demo_user_id, "session_id": "s"},
            headers=auth_headers,
        )

        r = await client.get(
            f"/api/v1/agents/{demo_user_id}/episodes",
            headers=auth_headers,
        )
        assert r.status_code == 200
        contents = [item.get("content", "") for item in r.json()]
        assert any(unique_content in c for c in contents), \
            "Written memory not found in read response"

    @pytest.mark.asyncio
    async def test_user_id_mismatch_rejected(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Writing a memory for a different user_id returns 403."""
        other_id = str(uuid.uuid4())
        r = await client.post(
            f"/api/v1/agents/{other_id}/episodes",
            json={"content": "cross-user write", "user_id": other_id, "session_id": "s"},
            headers=auth_headers,
        )
        assert r.status_code in (403, 422), r.text


# ──────────────────────────────────────────────────────────────────────────────
# Task 6.1: App-ID / Cross-Tenant Isolation
# ──────────────────────────────────────────────────────────────────────────────

class TestCrossTenantIsolation:
    """
    CRITICAL SECURITY INVARIANT: User A must never be able to read User B's data.
    This tests the most important correctness property of the memory layer.
    """

    @pytest.mark.asyncio
    async def test_user_a_cannot_read_user_b_memories(self, client: AsyncClient):
        """
        Register two separate users. User A writes a unique secret memory.
        User B must not be able to retrieve it.
        """
        uid = uuid.uuid4().hex[:6]

        # Register + login User A
        creds_a = {"email": f"user_a_{uid}@test.com", "password": "PassA!123"}
        await client.post("/api/v1/auth/register", json=creds_a)
        login_a = await client.post("/api/v1/auth/login", json=creds_a)
        headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}
        user_a_id = (await client.get("/api/v1/auth/me", headers=headers_a)).json()["id"]

        # Register + login User B
        creds_b = {"email": f"user_b_{uid}@test.com", "password": "PassB!123"}
        await client.post("/api/v1/auth/register", json=creds_b)
        login_b = await client.post("/api/v1/auth/login", json=creds_b)
        headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
        user_b_id = (await client.get("/api/v1/auth/me", headers=headers_b)).json()["id"]

        # User A writes a unique secret
        secret = f"secret_{uuid.uuid4().hex}"
        await client.post(
            f"/api/v1/agents/{user_a_id}/episodes",
            json={"content": secret, "user_id": user_a_id, "session_id": "s"},
            headers=headers_a,
        )

        # User B tries to read User A's episodes
        r = await client.get(
            f"/api/v1/agents/{user_a_id}/episodes",
            headers=headers_b,
        )

        # Must either be forbidden OR return empty (never the secret)
        if r.status_code == 200:
            contents = str(r.json())
            assert secret not in contents, \
                "CRITICAL SECURITY FAILURE: Cross-tenant data leakage detected!"
        else:
            assert r.status_code in (403, 404), \
                f"Unexpected status code: {r.status_code}"

    @pytest.mark.asyncio
    async def test_api_keys_are_user_scoped(self, client: AsyncClient):
        """API keys created by User A must not appear in User B's key list."""
        uid = uuid.uuid4().hex[:6]

        creds_a = {"email": f"key_a_{uid}@test.com", "password": "PassA!123"}
        await client.post("/api/v1/auth/register", json=creds_a)
        r = await client.post("/api/v1/auth/login", json=creds_a)
        headers_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

        creds_b = {"email": f"key_b_{uid}@test.com", "password": "PassB!123"}
        await client.post("/api/v1/auth/register", json=creds_b)
        r = await client.post("/api/v1/auth/login", json=creds_b)
        headers_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # User A creates a key
        key_name = f"user_a_key_{uid}"
        await client.post(
            "/api/v1/auth/api-keys",
            json={"name": key_name},
            headers=headers_a,
        )

        # User B lists their keys — must not contain User A's key
        r = await client.get("/api/v1/auth/api-keys", headers=headers_b)
        if r.status_code == 200:
            key_names = [k.get("name", "") for k in r.json()]
            assert key_name not in key_names, "API key isolation failed!"
