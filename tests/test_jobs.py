"""Job API tests with mocked Whisper."""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest

from tests.conftest import login


def _audio_file(name: str = "sample.mp3", payload: bytes | None = None) -> tuple[str, BytesIO, str]:
    content = payload if payload is not None else b"ID3\x03\x00\x00\x00fake-mp3-audio-content"
    return (name, BytesIO(content), "audio/mpeg")


async def _wait_for_status(client, job_id: str, statuses: set[str], timeout: float = 5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    last = None
    while asyncio.get_event_loop().time() < deadline:
        res = await client.get(f"/api/jobs/{job_id}")
        assert res.status_code == 200
        last = res.json()
        if last["status"] in statuses:
            return last
        await asyncio.sleep(0.15)
    raise AssertionError(f"Timeout waiting for {statuses}; last={last}")


@pytest.mark.asyncio
async def test_upload_validation_rejects_bad_extension(client):
    await login(client)
    files = {"file": ("notes.txt", BytesIO(b"hello"), "text/plain")}
    data = {"model": "large", "language": "Bulgarian"}
    res = await client.post("/api/jobs", files=files, data=data)
    assert res.status_code == 400
    assert "not allowed" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_validation_rejects_non_media(client):
    await login(client)
    files = {"file": ("evil.mp3", BytesIO(b"NOTMEDIA" + b"0" * 64), "audio/mpeg")}
    data = {"model": "large", "language": "Bulgarian"}
    res = await client.post("/api/jobs", files=files, data=data)
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_create_job_and_complete(client):
    await login(client)
    files = {"file": _audio_file()}
    data = {"model": "large", "language": "Bulgarian"}
    res = await client.post("/api/jobs", files=files, data=data)
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["status"] == "queued"
    assert created["model_name"] == "large"
    assert created["language"] == "Bulgarian"

    job = await _wait_for_status(client, created["id"], {"completed", "failed"})
    assert job["status"] == "completed"

    result = await client.get(f"/api/jobs/{created['id']}/result")
    assert result.status_code == 200
    body = result.json()
    assert "тестова транскрипция" in (body["transcription_text"] or "")
    assert "txt" in body["available_formats"]

    dl = await client.get(f"/api/jobs/{created['id']}/download?format=txt")
    assert dl.status_code == 200
    assert "тестова транскрипция" in dl.text

    from app.main import app

    engine = app.state.fake_engine
    assert engine.last_call["task"] == "transcribe"
    assert engine.last_call["model_name"] == "large"
    assert engine.last_call["language"] == "Bulgarian"


@pytest.mark.asyncio
async def test_list_and_delete_job(client):
    await login(client)
    files = {"file": _audio_file("clip.wav")}
    data = {"model": "small", "language": "English"}
    res = await client.post("/api/jobs", files=files, data=data)
    assert res.status_code == 201
    job_id = res.json()["id"]
    await _wait_for_status(client, job_id, {"completed", "failed"})

    listed = await client.get("/api/jobs")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] >= 1
    assert any(j["id"] == job_id for j in payload["jobs"])

    deleted = await client.delete(f"/api/jobs/{job_id}")
    assert deleted.status_code == 200

    missing = await client.get(f"/api/jobs/{job_id}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_job_failure_surfaces_error(client):
    await login(client)
    from app.main import app

    app.state.fake_engine.fail_next = True

    files = {"file": _audio_file("bad.mp3")}
    data = {"model": "large", "language": "Bulgarian"}
    res = await client.post("/api/jobs", files=files, data=data)
    assert res.status_code == 201
    job = await _wait_for_status(client, res.json()["id"], {"failed", "completed"})
    assert job["status"] == "failed"
    assert job["error_message"]


@pytest.mark.asyncio
async def test_unsupported_model_rejected(client):
    await login(client)
    files = {"file": _audio_file()}
    data = {"model": "not-a-real-model", "language": "Bulgarian"}
    res = await client.post("/api/jobs", files=files, data=data)
    assert res.status_code == 400
