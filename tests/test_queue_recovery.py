"""Queue recovery and persistence tests."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.database import Database
from app.models import JobStatus
from tests.conftest import FakeWhisperEngine, fake_probe_media, login


@pytest.mark.asyncio
async def test_fail_interrupted_processing_jobs(tmp_path):
    """Interrupted jobs must fail (not requeue) to avoid large-model OOM loops."""
    db_path = tmp_path / "db" / "recover.db"
    db_path.parent.mkdir(parents=True)
    db = Database(str(db_path))
    await db.connect()
    try:
        job = await db.create_job(
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "original_filename": "a.mp3",
                "stored_filename": "a.mp3",
                "upload_path": str(tmp_path / "a.mp3"),
                "file_size": 10,
                "content_type": "audio/mpeg",
                "model_name": "large",
                "language": "Bulgarian",
                "task": "transcribe",
            }
        )
        await db.update_job(job["id"], status=JobStatus.PREPARING.value)
        await db.update_job(
            job["id"],
            status=JobStatus.PROCESSING.value,
            progress_message="Was processing",
        )
        count = await db.requeue_interrupted_jobs()
        assert count == 1
        recovered = await db.get_job(job["id"])
        assert recovered is not None
        assert recovered["status"] == JobStatus.FAILED.value
        assert "Interrupted" in (recovered["error_message"] or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_persistent_job_survives_db_reconnect(tmp_path):
    db_path = tmp_path / "db" / "persist.db"
    db_path.parent.mkdir(parents=True)

    db1 = Database(str(db_path))
    await db1.connect()
    created = await db1.create_job(
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "original_filename": "persist.mp3",
            "stored_filename": "persist.mp3",
            "upload_path": str(tmp_path / "persist.mp3"),
            "file_size": 42,
            "content_type": "audio/mpeg",
            "model_name": "large",
            "language": "Bulgarian",
        }
    )
    await db1.close()

    db2 = Database(str(db_path))
    await db2.connect()
    try:
        loaded = await db2.get_job(created["id"])
        assert loaded is not None
        assert loaded["original_filename"] == "persist.mp3"
        assert loaded["status"] == JobStatus.QUEUED.value
        total = await db2.count_jobs()
        assert total == 1
    finally:
        await db2.close()


@pytest_asyncio.fixture
async def recovery_client(monkeypatch, tmp_path):
    upload = tmp_path / "uploads"
    output = tmp_path / "outputs"
    dbdir = tmp_path / "db"
    models = tmp_path / "models"
    for p in (upload, output, dbdir, models):
        p.mkdir()

    os.environ.update(
        {
            "UPLOAD_DIR": str(upload),
            "OUTPUT_DIR": str(output),
            "DB_PATH": str(dbdir / "test.db"),
            "WHISPER_MODEL_DIR": str(models),
            "DATA_DIR": str(tmp_path),
            "SESSION_HTTPS_ONLY": "false",
            "APP_PASSWORD": "test-password-123",
            "SESSION_SECRET": "test-session-secret-please-change-32chars",
            "APP_ENV": "test",
            "AUTO_DELETE_UPLOADS_DAYS": "0",
            "WORKER_POLL_INTERVAL_SECONDS": "0.2",
            "RUN_EMBEDDED_WORKER": "true",
            "WHISPER_DEVICE": "cpu",
        }
    )
    get_settings.cache_clear()

    upload_file = upload / "33333333-3333-3333-3333-333333333333.mp3"
    upload_file.write_bytes(b"ID3fakeaudio")

    db = Database(str(dbdir / "test.db"))
    await db.connect()
    await db.create_job(
        {
            "id": "33333333-3333-3333-3333-333333333333",
            "original_filename": "interrupted.mp3",
            "stored_filename": upload_file.name,
            "upload_path": str(upload_file),
            "file_size": upload_file.stat().st_size,
            "content_type": "audio/mpeg",
            "model_name": "large",
            "language": "Bulgarian",
        }
    )
    await db.update_job(
        "33333333-3333-3333-3333-333333333333",
        status=JobStatus.PREPARING.value,
    )
    await db.update_job(
        "33333333-3333-3333-3333-333333333333",
        status=JobStatus.PROCESSING.value,
        progress_message="Interrupted mid-run",
    )
    await db.close()

    from app.main import app

    fake = FakeWhisperEngine()
    monkeypatch.setattr("app.whisper_engine.WhisperEngine", lambda settings: fake)
    monkeypatch.setattr("app.media.probe_media", fake_probe_media)

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await login(ac)
            res = await ac.get("/api/jobs/33333333-3333-3333-3333-333333333333")
            ac._recovered_job = res.json()  # type: ignore[attr-defined]
            yield ac


@pytest.mark.asyncio
async def test_worker_marks_interrupted_job_failed(recovery_client):
    job = recovery_client._recovered_job  # type: ignore[attr-defined]
    assert job is not None
    assert job["status"] == "failed"
    assert job["original_filename"] == "interrupted.mp3"
    assert "Interrupted" in (job.get("error_message") or "")
