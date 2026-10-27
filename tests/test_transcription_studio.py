"""Tests for MrBit Transcription Studio."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.transcript_segments import (
    format_srt_timestamp,
    join_segment_texts,
    normalize_whisper_segments,
    segments_to_srt,
    segments_to_vtt,
    validate_split_at,
)
from tests.conftest import login


def test_normalize_whisper_segments_real_timestamps():
    result = {
        "segments": [
            {"start": 0.0, "end": 1.5, "text": " Здравей"},
            {"start": 1.5, "end": 3.0, "text": " свят"},
        ]
    }
    segs, has_words = normalize_whisper_segments(result)
    assert len(segs) == 2
    assert has_words is False
    assert segs[0]["start_sec"] == 0.0
    assert segs[0]["original_text"] == "Здравей"
    assert segs[1]["edited_text"] == "свят"


def test_normalize_empty_does_not_invent():
    segs, _ = normalize_whisper_segments({"segments": []})
    assert segs == []
    segs2, _ = normalize_whisper_segments({})
    assert segs2 == []


def test_validate_split_requires_interior_point():
    with pytest.raises(ValueError):
        validate_split_at(1.0, 2.0, 1.0)
    with pytest.raises(ValueError):
        validate_split_at(1.0, 2.0, 2.0)
    validate_split_at(1.0, 2.0, 1.5)


def test_srt_vtt_unicode_bulgarian():
    segs = [
        {
            "id": "a",
            "position": 0,
            "start_sec": 0.0,
            "end_sec": 1.2,
            "original_text": "Здравейте",
            "edited_text": "Здравейте, колеги!",
            "is_edited": True,
        }
    ]
    srt = segments_to_srt(segs, use_edited=True)
    assert "Здравейте, колеги!" in srt
    assert format_srt_timestamp(0.0) in srt
    vtt = segments_to_vtt(segs, use_edited=False)
    assert "Здравейте" in vtt
    assert "WEBVTT" in vtt
    assert join_segment_texts(segs, use_edited=True) == "Здравейте, колеги!"


@pytest.mark.asyncio
async def test_studio_auth_required(client):
    res = await client.get("/api/jobs/nope/segments")
    assert res.status_code == 401
    res2 = await client.get("/api/jobs/nope/audio")
    assert res2.status_code == 401


@pytest.mark.asyncio
async def test_segments_persist_edit_restore_split_merge_conflict_export(client):
    await login(client)

    files = {"file": ("sample.mp3", b"ID3fakeaudio", "audio/mpeg")}
    created = await client.post(
        "/api/jobs", files=files, data={"model": "small", "language": "Bulgarian"}
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    job = None
    for _ in range(50):
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        if job["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.1)
    assert job["status"] == "completed"

    segs = await client.get(f"/api/jobs/{job_id}/segments")
    assert segs.status_code == 200, segs.text
    body = segs.json()
    assert body["segments_status"] == "ready"
    assert body["segments"]
    assert body["transcript_revision"] == 0
    seg = body["segments"][0]
    assert seg["start"] == 0.0
    assert "тестова" in seg["original_text"].lower() or seg["original_text"]

    # Edit
    patched = await client.patch(
        f"/api/jobs/{job_id}/segments/{seg['id']}",
        json={"edited_text": "Коригиран текст на български.", "expected_revision": 0},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["transcript_revision"] == 1
    assert patched.json()["segment"]["is_edited"] is True

    # Conflict
    conflict = await client.patch(
        f"/api/jobs/{job_id}/segments/{seg['id']}",
        json={"edited_text": "stale", "expected_revision": 0},
    )
    assert conflict.status_code == 409

    # Restore
    restored = await client.post(
        f"/api/jobs/{job_id}/segments/{seg['id']}/restore?expected_revision=1"
    )
    assert restored.status_code == 200
    assert restored.json()["segment"]["edited_text"] == seg["original_text"]

    rev = restored.json()["transcript_revision"]

    # Ensure at least two segments for merge — split first if only one
    listing = (await client.get(f"/api/jobs/{job_id}/segments")).json()
    segments = listing["segments"]
    if len(segments) == 1:
        s0 = segments[0]
        mid = (s0["start"] + s0["end"]) / 2
        # Fake engine uses end 1.23 — mid is valid
        split = await client.post(
            f"/api/jobs/{job_id}/segments/split",
            json={
                "segment_id": s0["id"],
                "split_at": mid,
                "expected_revision": rev,
                "left_text": "Лява част",
                "right_text": "Дясна част",
            },
        )
        assert split.status_code == 200, split.text
        rev = split.json()["transcript_revision"]
        segments = split.json()["segments"]
        assert len(segments) == 2

    left, right = segments[0], segments[1]
    merged = await client.post(
        f"/api/jobs/{job_id}/segments/merge",
        json={
            "left_id": left["id"],
            "right_id": right["id"],
            "expected_revision": rev,
        },
    )
    assert merged.status_code == 200, merged.text
    assert len(merged.json()["segments"]) == 1

    revisions = await client.get(f"/api/jobs/{job_id}/revisions")
    assert revisions.status_code == 200
    assert revisions.json()["revisions"]

    # Audio authenticated stream
    audio = await client.get(f"/api/jobs/{job_id}/audio")
    assert audio.status_code == 200
    assert audio.content

    # Range
    ranged = await client.get(
        f"/api/jobs/{job_id}/audio",
        headers={"Range": "bytes=0-3"},
    )
    assert ranged.status_code == 206
    assert ranged.headers.get("content-range", "").startswith("bytes 0-3/")

    # Export edited SRT with Bulgarian
    export = await client.get(
        f"/api/jobs/{job_id}/export?format=srt&source=edited"
    )
    assert export.status_code == 200
    assert "WEBVTT" not in export.text
    assert "-->" in export.text

    txt = await client.get(f"/api/jobs/{job_id}/export?format=txt&source=original")
    assert txt.status_code == 200
    assert len(txt.text.strip()) > 0

    # Studio page
    page = await client.get(f"/studio/{job_id}")
    assert page.status_code == 200
    assert "Transcription Studio" in page.text


@pytest.mark.asyncio
async def test_legacy_job_without_timestamps_unsupported(client):
    await login(client)

    # Create completed job manually without segments / without whisper json segments
    from app.config import get_settings
    from app.database import Database
    from app.models import JobStatus, SegmentsStatus

    settings = get_settings()
    db = Database(settings.db_path)
    await db.connect()
    job_id = "legacy-job-no-ts"
    now = "2020-01-01T00:00:00+00:00"
    await db.conn.execute(
        """
        INSERT INTO jobs (
          id, original_filename, stored_filename, upload_path, file_size,
          model_name, language, task, status, transcription_text,
          created_at, updated_at, segments_status, transcript_revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "old.mp3",
            "old.mp3",
            "",
            10,
            "small",
            "Bulgarian",
            "transcribe",
            JobStatus.COMPLETED.value,
            "Само TXT без timestamps.",
            now,
            now,
            SegmentsStatus.PENDING.value,
            0,
        ),
    )
    await db.conn.commit()
    await db.close()

    res = await client.get(f"/api/jobs/{job_id}/segments")
    assert res.status_code == 200
    data = res.json()
    assert data["segments_status"] == "unsupported"
    assert data["segments"] == []

    # Timed edited export must not invent timestamps
    export = await client.get(
        f"/api/jobs/{job_id}/export?format=srt&source=edited"
    )
    assert export.status_code == 409

    # TXT original still works
    txt = await client.get(f"/api/jobs/{job_id}/export?format=txt&source=original")
    assert txt.status_code == 200
    assert "Само TXT" in txt.text


@pytest.mark.asyncio
async def test_bulk_save_and_worker_compatibility(client):
    await login(client)
    files = {"file": ("a.wav", b"RIFFfakewavdataxx", "audio/wav")}
    created = await client.post(
        "/api/jobs", files=files, data={"model": "small", "language": "English"}
    )
    job_id = created.json()["id"]
    for _ in range(50):
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        if job["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.1)
    assert job["status"] == "completed"

    segs = (await client.get(f"/api/jobs/{job_id}/segments")).json()["segments"]
    assert segs
    sid = segs[0]["id"]
    saved = await client.post(
        f"/api/jobs/{job_id}/segments/save",
        json={
            "expected_revision": 0,
            "changes": [{"id": sid, "edited_text": "Hello, everyone!", "edit_source": "manual"}],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["transcript_revision"] == 1
    assert any(s["edited_text"] == "Hello, everyone!" for s in saved.json()["segments"])

    # Existing download still works
    dl = await client.get(f"/api/jobs/{job_id}/download?format=txt")
    assert dl.status_code == 200
