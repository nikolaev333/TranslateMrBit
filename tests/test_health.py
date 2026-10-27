"""Health endpoint tests — must stay torch-free."""

from __future__ import annotations

import sys

import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    res = await client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert data["device"] in {"cpu", "cuda", "auto", "unknown"}
    # null model_loaded is healthy — API does not keep models in-process.
    assert "model_loaded" in data


@pytest.mark.asyncio
async def test_health_does_not_import_torch(client):
    """Coolify probes /health; importing torch here would hang/OOM the API."""
    before = {name for name in sys.modules if name == "torch" or name.startswith("torch.")}
    res = await client.get("/health")
    assert res.status_code == 200
    after = {name for name in sys.modules if name == "torch" or name.startswith("torch.")}
    assert after == before


@pytest.mark.asyncio
async def test_root_requires_auth_or_redirects(client):
    res = await client.get("/", follow_redirects=False)
    assert res.status_code in {200, 303, 401, 403}
