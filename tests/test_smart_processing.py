"""MrBit Smart Processing Engine tests (mocked inference)."""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest

from app.inference.base import TranscriptionResult
from app.inference.backend_registry import get_backend, list_backends
from app.inference.model_profiles import get_model_profile, list_model_profiles
from app.services.smart_processing.chunking import merge_chunk_segments, plan_fixed_chunks
from app.services.smart_processing.model_recommender import (
    recommend_models,
    validate_model_choice,
)
from app.services.smart_processing.resource_monitor import (
    collect_resources,
    effective_available_ram_gb,
)
from app.services.smart_processing.scheduler import can_admit_job
from tests.conftest import login


def _audio_file(name: str = "sample_sp.mp3") -> tuple[str, BytesIO, str]:
    return (name, BytesIO(b"ID3\x03\x00\x00\x00fake-mp3-audio-content"), "audio/mpeg")


@pytest.mark.asyncio
async def test_resources_endpoint_auth(client):
    res = await client.get("/api/system/resources")
    assert res.status_code == 401
    await login(client)
    res = await client.get("/api/system/resources")
    assert res.status_code == 200
    body = res.json()
    assert "memory" in body
    assert "cpu_count_logical" in body
    assert body["gpu"]["available"] in {True, False}


def test_resource_monitor_no_fake_values():
    data = collect_resources()
    mem = data["memory"]
    # Missing metrics must be null, never invented numbers for GPU when unavailable
    if not data["gpu"]["available"]:
        assert data["gpu"]["devices"] == []
    assert "effective_available_bytes" in mem
    _ = effective_available_ram_gb(data)


def test_model_recommendation_never_swaps():
    resources = {
        "memory": {
            "effective_available_bytes": int(2.0 * 1024**3),
            "host_available_bytes": int(2.0 * 1024**3),
        }
    }
    report = recommend_models(
        device="cpu",
        backend="openai-whisper",
        safety_margin_gb=0.5,
        resources=resources,
    )
    assert report["preferred"] is not None
    assert report["preferred"]["model_id"] != "large"
    unsafe = validate_model_choice(
        "large",
        device="cpu",
        backend="openai-whisper",
        safety_margin_gb=0.5,
        allow_unsafe=False,
        resources=resources,
    )
    assert unsafe["ok"] is False
    assert unsafe["code"] == "unsafe_resources"
    forced = validate_model_choice(
        "large",
        device="cpu",
        backend="openai-whisper",
        safety_margin_gb=0.5,
        allow_unsafe=True,
        resources=resources,
    )
    assert forced["ok"] is True
    assert forced["model_id"] == "large"


def test_model_profiles_and_backends():
    profiles = list_model_profiles()
    assert any(p["model_id"] == "tiny" for p in profiles)
    assert get_model_profile("small") is not None
    from app.config import Settings

    backends = list_backends(Settings())
    ids = {b["backend_id"] for b in backends}
    assert "openai-whisper" in ids
    assert "faster-whisper" in ids


def test_normalized_transcription_result():
    tr = TranscriptionResult(
        text="hello",
        language="en",
        duration=1.0,
        segments=[{"start": 0.0, "end": 1.0, "text": "hello"}],
        model_name="tiny",
        backend="openai-whisper",
    )
    compat = tr.to_whisper_compat()
    assert compat["text"] == "hello"
    assert compat["segments"][0]["start"] == 0.0
    assert "confidence" not in compat  # never invent confidence


def test_chunk_planning_and_merge():
    from app.services.smart_processing.chunking import AudioChunk

    chunks = plan_fixed_chunks(1250.0, chunk_sec=600.0, overlap_sec=1.0)
    assert len(chunks) >= 3
    assert chunks[0].start_sec == 0.0
    assert abs(chunks[-1].end_sec - 1250.0) < 1e-6
    # Overlapping windows with duplicate boundary text must dedupe once.
    c0 = AudioChunk(0, 0.0, 10.0)
    c1 = AudioChunk(1, 9.0, 20.0)
    merged = merge_chunk_segments(
        [
            (c0, [{"start": 8.0, "end": 9.5, "text": "one"}]),
            (
                c1,
                [
                    {"start": 0.0, "end": 1.0, "text": "one"},
                    {"start": 2.0, "end": 3.0, "text": "two"},
                ],
            ),
        ]
    )
    texts = [m["text"] for m in merged]
    assert texts.count("one") == 1
    assert "two" in texts
    assert merged[0]["start"] == 8.0
    assert merged[-1]["start"] == pytest.approx(11.0)


def test_scheduler_admission():
    resources = {
        "memory": {"effective_available_bytes": int(12 * 1024**3)},
        "cpu_percent": 10.0,
    }
    ok = can_admit_job(
        {"model_name": "small", "cancel_requested": 0, "priority": 100},
        running_count=0,
        max_concurrent=1,
        resources=resources,
        safety_margin_gb=0.5,
        enforce_memory_gate=True,
    )
    assert ok.admit is True
    blocked = can_admit_job(
        {"model_name": "large", "cancel_requested": 0},
        running_count=0,
        max_concurrent=1,
        resources={"memory": {"effective_available_bytes": int(2 * 1024**3)}},
        safety_margin_gb=0.5,
        enforce_memory_gate=True,
    )
    assert blocked.admit is False
    assert blocked.code == "waiting_resources"


@pytest.mark.asyncio
async def test_atomic_claim_and_priority(tmp_path):
    from app.database import Database
    from app.models import JobStatus

    db = Database(str(tmp_path / "claim.db"))
    await db.connect()
    try:
        await db.create_job(
            {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "original_filename": "low.mp3",
                "stored_filename": "low.mp3",
                "upload_path": str(tmp_path / "low.mp3"),
                "file_size": 1,
                "model_name": "tiny",
                "language": "en",
                "priority": 200,
            }
        )
        await db.create_job(
            {
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "original_filename": "high.mp3",
                "stored_filename": "high.mp3",
                "upload_path": str(tmp_path / "high.mp3"),
                "file_size": 1,
                "model_name": "tiny",
                "language": "en",
                "priority": 10,
            }
        )
        first = await db.claim_next_job(worker_id="w1", max_concurrent=1)
        assert first is not None
        # Higher priority number wins (200 before 10)
        assert first["id"].startswith("aaaaaaaa")
        assert first["status"] == JobStatus.PREPARING.value
        # Second worker cannot claim while concurrency=1
        second = await db.claim_next_job(worker_id="w2", max_concurrent=1)
        assert second is None
        # Raise concurrency: next claim is the remaining lower-priority job
        third = await db.claim_next_job(worker_id="w2", max_concurrent=2)
        assert third is not None
        assert third["id"].startswith("bbbbbbbb")
        assert third["id"] != first["id"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cancel_and_retry_api(client):
    await login(client)
    files = {"file": _audio_file("cancel_me.mp3")}
    data = {
        "model": "tiny",
        "language": "Bulgarian",
        "priority": "50",
        "inference_backend": "openai-whisper",
        "allow_unsafe_model": "true",
    }
    res = await client.post("/api/jobs", files=files, data=data)
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    # Cancel quickly (may already be completing with fake engine)
    cancel = await client.post(f"/api/processing/jobs/{job_id}/cancel")
    assert cancel.status_code == 200

    # Wait until terminal
    deadline = asyncio.get_event_loop().time() + 8
    last = None
    while asyncio.get_event_loop().time() < deadline:
        last = (await client.get(f"/api/processing/jobs/{job_id}")).json()
        if last["status"] in {"completed", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.15)

    assert last is not None
    if last["status"] in {"failed", "cancelled"}:
        retry = await client.post(f"/api/processing/jobs/{job_id}/retry")
        assert retry.status_code == 200
        assert retry.json()["job"]["status"] == "queued"


@pytest.mark.asyncio
async def test_processing_queue_and_models_api(client):
    await login(client)
    q = await client.get("/api/processing/queue")
    assert q.status_code == 200
    assert "jobs" in q.json()
    m = await client.get("/api/models")
    assert m.status_code == 200
    body = m.json()
    assert "profiles" in body
    assert "backends" in body
    rec = await client.get("/api/models/recommendations")
    assert rec.status_code == 200
    assert "recommendations" in rec.json()
    metrics = await client.get("/api/processing/metrics")
    assert metrics.status_code == 200
    page = await client.get("/processing")
    assert page.status_code == 200
    assert "MrBit Smart Processing" in page.text


@pytest.mark.asyncio
async def test_openai_backend_via_registry(client):
    await login(client)
    from app.config import get_settings
    from app.main import app

    settings = get_settings()
    backend = get_backend("openai-whisper", settings, whisper_engine=app.state.fake_engine)
    result = backend.transcribe(
        "dummy.wav",
        model_name="tiny",
        language="Bulgarian",
    )
    assert result.text
    assert result.backend == "openai-whisper"
    assert result.duration == 1.23
