"""Tests for Phase 1: Auth Enforcement."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.database import get_db
from app.models.user import User, APIKey
from app.core.security import get_password_hash
import uuid

client = TestClient(app)

# Test user data
TEST_USER_ID = str(uuid.uuid4())
TEST_USER_EMAIL = "test@example.com"
TEST_USER_PASSWORD = "testpassword123"


@pytest.fixture(scope="module")
def test_user():
    """Create a test user."""
    from app.database import engine
    from sqlalchemy.orm import Session
    
    # Create tables
    from app.models.memory import Base
    Base.metadata.create_all(bind=engine)
    
    # Create user
    with Session(engine) as db:
        user = User(
            email=TEST_USER_EMAIL,
            hashed_password=get_password_hash(TEST_USER_PASSWORD),
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        yield user
        
        # Cleanup
        db.query(User).filter(User.id == user.id).delete()
        db.commit()


@pytest.fixture(scope="module")
def auth_headers(test_user):
    """Get auth headers for test user."""
    from app.core.security import create_access_token
    token = create_access_token(data={"sub": str(test_user.id)})
    return {"Authorization": f"Bearer {token["access_token"]}"}


@pytest.fixture(scope="module")
def api_key_headers(test_user):
    """Get API key headers for test user."""
    from app.core.security import get_password_hash
    import secrets
    
    raw_key = f"mem_{secrets.token_urlsafe(32)}"
    key_hash = get_password_hash(raw_key)
    
    from app.database import engine
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        api_key = APIKey(
            user_id=test_user.id,
            key_hash=key_hash,
            name="Test Key",
            is_active=True,
        )
        db.add(api_key)
        db.commit()
    
    return {"Authorization": f"ApiKey {raw_key}"}


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
