"""
Task 6.1: Integration tests for the unified write flow and app_id context isolation.
Tests that memories written under one app_id are never readable by another.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient


class TestUnifiedWrite:
    """Tests for the memory write endpoint (episodic store)."""

    @pytest.mark.asyncio
    async def test_write_episodic_memory_success(self, client: AsyncClient, auth_headers: dict):
        """A valid write request should return 200 with a memory ID."""
        payload = {
            "content": "The user prefers Python over JavaScript.",
            "user_id": "test_user_001",
            "session_id": "session_abc",
        }
        response = await client.post(
            "/api/v1/episodic/", json=payload, headers=auth_headers
        )
        assert response.status_code in (200, 201), response.text
        body = response.json()
        assert "id" in body

    @pytest.mark.asyncio
    async def test_write_requires_authentication(self, client: AsyncClient):
        """Writing without an auth token must be rejected with 401/403."""
        payload = {
            "content": "This should fail.",
            "user_id": "test_user_001",
            "session_id": "session_abc",
        }
        response = await client.post("/api/v1/episodic/", json=payload)
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_empty_content_rejected(self, client: AsyncClient, auth_headers: dict):
        """Empty content strings should fail validation."""
        payload = {
            "content": "",
            "user_id": "test_user_001",
            "session_id": "session_abc",
        }
        response = await client.post(
            "/api/v1/episodic/", json=payload, headers=auth_headers
        )
        assert response.status_code == 422


class TestAppIdIsolation:
    """
    Task 6.1: Verify that app_id context isolation prevents cross-tenant data leakage.
    This is the most critical security invariant of the system.
    """

    @pytest.mark.asyncio
    async def test_episodic_read_isolation(self, client: AsyncClient):
        """
        User A's memories should NOT be returned when User B queries.
        This test registers two separate users and verifies data isolation.
        """
        # Register User A
        user_a_creds = {"email": "user_a_isolation@test.com", "password": "pass_A_123"}
        await client.post("/api/v1/auth/register", json=user_a_creds)
        r = await client.post("/api/v1/auth/login", json=user_a_creds)
        headers_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # Register User B
        user_b_creds = {"email": "user_b_isolation@test.com", "password": "pass_B_123"}
        await client.post("/api/v1/auth/register", json=user_b_creds)
        r = await client.post("/api/v1/auth/login", json=user_b_creds)
        headers_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # User A writes a unique memory
        secret_memory = "user_a_secret_xk92ms"
        await client.post(
            "/api/v1/episodic/",
            json={"content": secret_memory, "user_id": "user_a", "session_id": "s1"},
            headers=headers_a,
        )

        # User B tries to query the same content — should get nothing
        r = await client.get(
            "/api/v1/episodic/",
            params={"user_id": "user_a"},
            headers=headers_b,
        )
        # Either 403 (forbidden) or an empty list — never the other user's data
        if r.status_code == 200:
            items = r.json()
            assert not any(secret_memory in str(item) for item in items), \
                "CRITICAL: Cross-tenant data leakage detected!"
        else:
            assert r.status_code in (403, 404)
