"""Password validation tests for auth schemas and API requests."""

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.schemas.user import UserCreate, UserLogin


@pytest.mark.parametrize(
    "password",
    [
        "a",
        "lowercase1",
        "UPPERCASE1",
        "NoDigitsHere",
        "Aa1",
        "A" * 129 + "a1",
    ],
)
def test_user_create_rejects_weak_passwords(password: str):
    with pytest.raises(ValidationError):
        UserCreate(email="weak@example.com", password=password)


def test_user_create_accepts_strong_password():
    user = UserCreate(email="strong@example.com", password="StrongPass123")

    assert user.password == "StrongPass123"


def test_user_login_allows_legacy_weak_password_to_reach_verification():
    login = UserLogin(email="legacy@example.com", password="a")

    assert login.password == "a"


@pytest.mark.asyncio
async def test_register_rejects_weak_password(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "weak-api@example.com", "password": "a"},
    )

    assert response.status_code == 422
    assert "at least 8 characters" in response.text or "Password must" in response.text
