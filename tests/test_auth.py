"""Authentication tests."""

import pytest


@pytest.mark.asyncio
async def test_login_success(client):
    res = await client.post("/api/login", json={"password": "test-password-123"})
    assert res.status_code == 200
    assert "whisper_session" in res.cookies


@pytest.mark.asyncio
async def test_login_failure(client):
    res = await client.post("/api/login", json={"password": "wrong"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid password"


@pytest.mark.asyncio
async def test_protected_endpoint_requires_auth(client):
    res = await client.get("/api/jobs")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_logout(client):
    await client.post("/api/login", json={"password": "test-password-123"})
    res = await client.post("/api/logout")
    assert res.status_code == 200
    res = await client.get("/api/jobs")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_login_rate_limit(client, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "login_rate_limit", 3)
    monkeypatch.setattr(settings, "login_rate_window_seconds", 60)

    # Exhaust attempts
    for _ in range(3):
        await client.post("/api/login", json={"password": "wrong"})
    res = await client.post("/api/login", json={"password": "wrong"})
    assert res.status_code == 429
