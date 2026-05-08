"""Tests for Phase 1: Auth Enforcement."""

import os
import pytest
from fastapi.testclient import TestClient
from app.main import app
import uuid

client = TestClient(app)
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="requires live PostgreSQL/Supabase database; set RUN_DB_TESTS=1",
)

# Test user data
TEST_USER_ID = None
TEST_USER_EMAIL = f"test_{uuid.uuid4().hex}@example.com"
TEST_USER_PASSWORD = "TestPassword123"


@pytest.fixture(scope="module")
def test_user():
    """Create a test user through the public API."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD},
    )
    assert response.status_code in (201, 400)
    if response.status_code == 201:
        user = response.json()
    else:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD},
        )
        assert login.status_code == 200
        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        user = me.json()
    global TEST_USER_ID
    TEST_USER_ID = user["id"]
    yield user


@pytest.fixture(scope="module")
def auth_headers(test_user):
    """Get auth headers for test user."""
    return _auth_headers()


def _auth_headers():
    response = client.post(
        "/api/v1/auth/login",
        json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def api_key_headers(test_user):
    """Get API key headers for test user."""
    response = client.post(
        "/api/v1/auth/api-keys",
        json={"name": "Test Key"},
        headers=_auth_headers(),
    )
    assert response.status_code == 201
    return {"Authorization": f"ApiKey {response.json()['api_key']}"}


class TestAuthEnforcement:
    """Test auth enforcement on all endpoints."""
    
    def test_episodic_no_auth(self):
        """Test episodic endpoint without auth returns 401."""
        response = client.post(
            f"/api/v1/agents/{TEST_USER_ID}/episodes",
            json={
                "session_id": "test",
                "content": "Test content",
            },
        )
        assert response.status_code == 401
    
    def test_episodic_with_jwt(self, auth_headers):
        """Test episodic endpoint with JWT auth."""
        response = client.post(
            f"/api/v1/agents/{TEST_USER_ID}/episodes",
            json={
                "session_id": "test",
                "content": "Test content",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert "id" in response.json()
    
    def test_episodic_with_apikey(self, api_key_headers):
        """Test episodic endpoint with API key auth."""
        response = client.post(
            f"/api/v1/agents/{TEST_USER_ID}/episodes",
            json={
                "session_id": "test",
                "content": "Test content",
            },
            headers=api_key_headers,
        )
        assert response.status_code == 200
        assert "id" in response.json()
    
    def test_user_id_mismatch(self, auth_headers):
        """Test that user_id mismatch is rejected."""
        different_user_id = str(uuid.uuid4())
        response = client.post(
            f"/api/v1/agents/{different_user_id}/episodes",
            json={
                "session_id": "test",
                "content": "Test content",
            },
            headers=auth_headers,
        )
        assert response.status_code == 403
        assert "User ID mismatch" in response.json()["detail"]
    
    def test_semantic_no_auth(self):
        """Test semantic endpoint without auth returns 401."""
        response = client.post(
            f"/api/v1/agents/{TEST_USER_ID}/semantics",
            json={"content": "Test content"},
        )
        assert response.status_code == 401
    
    def test_graph_no_auth(self):
        """Test graph endpoint without auth returns 401."""
        response = client.post(
            f"/api/v1/agents/{TEST_USER_ID}/graph/nodes",
            json={
                "label": "Test Node",
                "type": "test",
            },
        )
        assert response.status_code == 401
    
    def test_rag_no_auth(self):
        """Test RAG endpoint without auth returns 401."""
        response = client.post(
            "/api/v1/rag/chat",
            json={
                "user_id": TEST_USER_ID,
                "message": "Hello",
            },
        )
        assert response.status_code == 401


class TestDemoModeAuthBypass:
    """Test that demo mode bypasses auth."""
    
    def test_demo_mode_no_auth_needed(self, monkeypatch):
        """In demo mode, auth should be bypassed."""
        monkeypatch.setattr("app.config.settings.demo_mode", True)
        
        # This should work without auth in demo mode
        # Note: This test is conceptual - actual demo mode
        # creates a synthetic user
        pass  # Implement based on your demo mode setup


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
