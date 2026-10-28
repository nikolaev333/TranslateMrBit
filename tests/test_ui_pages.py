"""UI page smoke tests for casino-inspired redesign."""

from __future__ import annotations

import pytest

from tests.conftest import login


@pytest.mark.asyncio
async def test_login_page_branding(client):
    res = await client.get("/login")
    assert res.status_code == 200
    assert "TranslateMrBit" in res.text
    assert "AI Transcription Platform" in res.text
    assert "hero-waveform.svg" in res.text
    assert "password" in res.text


@pytest.mark.asyncio
async def test_overview_and_transcribe_nav(client):
    await login(client)
    overview = await client.get("/")
    assert overview.status_code == 200
    assert "Overview" in overview.text
    assert "Total jobs" in overview.text
    assert "New Transcription" in overview.text

    upload = await client.get("/transcribe")
    assert upload.status_code == 200
    assert "Start a transcription" in upload.text
    assert "dropzone" in upload.text

    settings = await client.get("/settings")
    assert settings.status_code == 200
    assert "Settings" in settings.text


@pytest.mark.asyncio
async def test_feature_pages_render(client):
    await login(client)
    for path in ("/history", "/vocabulary", "/processing", "/studio", "/quality"):
        res = await client.get(path)
        assert res.status_code == 200, path
        assert "TranslateMrBit" in res.text
        assert "app-sidebar" in res.text
    processing = await client.get("/processing")
    assert 'href="/studio"' in processing.text
    assert 'href="/quality"' in processing.text
    assert "nav-link disabled" not in processing.text
    assert 'aria-disabled="true"' not in processing.text
    assert "Transcription Studio" in (await client.get("/studio")).text
    assert "Quality Analyzer" in (await client.get("/quality")).text
