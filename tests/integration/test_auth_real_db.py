"""Integration tests for the auth router against a real Postgres database.

Covers the security-critical paths the demo-mode tests cannot:
- registration persistence
- duplicate-email rejection
- correct vs incorrect password
- API key issuance / listing / revoke
- refresh-token round-trip
- protected endpoints reject unauthenticated requests
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio


async def test_registration_persists_user(http_client: AsyncClient) -> None:
    creds = {"email": "first@example.com", "password": "GoodPass1!"}
    r = await http_client.post("/api/v1/auth/register", json=creds)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "first@example.com"
    assert body["is_active"] is True
    assert "id" in body


async def test_duplicate_email_is_rejected(http_client: AsyncClient) -> None:
    creds = {"email": "dup@example.com", "password": "GoodPass1!"}
    first = await http_client.post("/api/v1/auth/register", json=creds)
    assert first.status_code == 201
    second = await http_client.post("/api/v1/auth/register", json=creds)
    assert second.status_code == 400
    assert "already registered" in second.json()["detail"].lower()


async def test_login_with_correct_password_returns_token(http_client: AsyncClient) -> None:
    creds = {"email": "login@example.com", "password": "GoodPass1!"}
    await http_client.post("/api/v1/auth/register", json=creds)
    login = await http_client.post("/api/v1/auth/login", json=creds)
    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20
    assert isinstance(body["refresh_token"], str) and len(body["refresh_token"]) > 20


async def test_login_with_wrong_password_is_rejected(http_client: AsyncClient) -> None:
    creds = {"email": "wrongpw@example.com", "password": "GoodPass1!"}
    await http_client.post("/api/v1/auth/register", json=creds)
    bad = await http_client.post(
        "/api/v1/auth/login", json={"email": creds["email"], "password": "Different!"}
    )
    assert bad.status_code == 401


async def test_protected_endpoint_requires_token(http_client: AsyncClient) -> None:
    r = await http_client.post("/api/v1/auth/api-keys", json={"name": "no-auth"})
    assert r.status_code == 401


async def test_me_returns_authenticated_user(fresh_user, http_client: AsyncClient) -> None:
    me = await http_client.get("/api/v1/auth/me", headers=fresh_user["headers"])
    assert me.status_code == 200
    assert me.json()["email"] == fresh_user["email"]
    assert me.json()["id"] == fresh_user["user_id"]


async def test_api_key_lifecycle(fresh_user, http_client: AsyncClient) -> None:
    # Create
    create = await http_client.post(
        "/api/v1/auth/api-keys",
        json={"name": "Test CLI"},
        headers=fresh_user["headers"],
    )
    assert create.status_code == 201
    raw_key = create.json()["api_key"]
    assert raw_key.startswith("mem_")
    key_id = create.json()["id"]

    # List
    listing = await http_client.get("/api/v1/auth/api-keys", headers=fresh_user["headers"])
    assert listing.status_code == 200
    names = [k["name"] for k in listing.json()]
    assert "Test CLI" in names
    # Raw key must NEVER be returned on list
    for k in listing.json():
        assert "api_key" not in k

    # Use the key as auth (ApiKey scheme)
    me_via_key = await http_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"ApiKey {raw_key}"}
    )
    assert me_via_key.status_code == 200
    assert me_via_key.json()["email"] == fresh_user["email"]

    # Revoke
    delete = await http_client.delete(
        f"/api/v1/auth/api-keys/{key_id}", headers=fresh_user["headers"]
    )
    assert delete.status_code == 204

    # Revoked key no longer authenticates
    me_after = await http_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"ApiKey {raw_key}"}
    )
    assert me_after.status_code == 401


async def test_refresh_token_issues_new_access_token(fresh_user, http_client: AsyncClient) -> None:
    # First login already gave us a refresh token implicitly through fresh_user;
    # the fixture stored access only. Re-login to grab the refresh token.
    login = await http_client.post(
        "/api/v1/auth/login",
        json={"email": fresh_user["email"], "password": fresh_user["password"]},
    )
    refresh = login.json()["refresh_token"]

    refreshed = await http_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh}
    )
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]
    # The new access token must work
    me = await http_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == fresh_user["email"]


async def test_invalid_refresh_token_rejected(http_client: AsyncClient) -> None:
    bad = await http_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "not.a.real.jwt"}
    )
    assert bad.status_code == 401
