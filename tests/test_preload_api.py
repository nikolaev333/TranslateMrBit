"""Preload API must enqueue without importing torch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.conftest import login


@pytest.mark.asyncio
async def test_preload_enqueues_without_torch(client, tmp_path):
    await login(client)
    before = {name for name in sys.modules if name == "torch" or name.startswith("torch.")}
    res = await client.post("/api/models/preload", json={"models": ["small"]})
    assert res.status_code == 200, res.text
    after = {name for name in sys.modules if name == "torch" or name.startswith("torch.")}
    assert after == before

    body = res.json()
    assert "queued" in body["message"].lower() or "download" in body["message"].lower()

    # Request file lives next to SQLite (shared volume between API and worker).
    from app.main import app

    request_file = Path(app.state.settings.db_path).parent / "preload_request.json"
    assert request_file.is_file()
    payload = json.loads(request_file.read_text(encoding="utf-8"))
    assert payload["models"] == ["small"]

    status = await client.get("/api/models/preload/status")
    assert status.status_code == 200
    snap = status.json()
    assert "models" in snap
    assert any(m["name"] == "small" for m in snap["models"])
