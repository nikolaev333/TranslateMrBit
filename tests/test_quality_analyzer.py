"""Synthetic tests for MrBit Quality Analyzer."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest

from app.services.quality_analyzer.analyzer import AnalyzerContext, QualityAnalyzer
from app.services.quality_analyzer.text_metrics import (
    detect_immediate_word_repetitions,
    detect_phrase_repetitions,
    intentional_repetition_heuristic,
)
from app.services.quality_analyzer.timestamp_analysis import analyze_timestamps
from app.services.wer import character_error_rate, word_error_rate
from tests.conftest import login


def test_repeated_words_and_phrases_unicode():
    hits = detect_immediate_word_repetitions("This is is a sample sentence.")
    assert any(h["phrase"] == "is" and h["count"] >= 2 for h in hits)

    hello = detect_immediate_word_repetitions("Hello hello hello hello.")
    assert any(h["count"] >= 4 for h in hello)

    bg = detect_immediate_word_repetitions("това това е пример")
    assert any(h["phrase"] == "това" for h in bg)

    phrases = detect_phrase_repetitions(
        "This is an example. This is an example. This is an example."
    )
    assert any(h["count"] >= 2 for h in phrases)


def test_intentional_repetition_not_auto_error():
    assert intentional_repetition_heuristic("да", 2) is True
    assert intentional_repetition_heuristic("hello", 4) is False


def test_timestamp_anomalies_no_invention():
    issues = analyze_timestamps(
        [
            {"id": "a", "start_sec": 0.0, "end_sec": 2.0},
            {"id": "b", "start_sec": 1.5, "end_sec": 3.0},
            {"id": "c", "start_sec": 10.0, "end_sec": 11.0},
        ],
        gap_threshold_sec=5.0,
    )
    types = {i["type"] for i in issues}
    assert "TS_OVERLAP" in types
    assert "TS_GAP" in types
    assert analyze_timestamps([{"id": "x", "text": "no times"}]) == []


def test_missing_whisper_metadata_does_not_fail():
    ctx = AnalyzerContext(
        segments=[{"id": "1", "start_sec": 0, "end_sec": 1, "edited_text": "SampleTranscript ok"}],
        full_text="SampleTranscript ok",
        whisper_segments_meta=[{}],  # missing fields
    )
    result = QualityAnalyzer().analyze(ctx)
    assert result.segments_analyzed == 1
    assert isinstance(result.warnings, list)


def test_compression_ratio_heuristic():
    ctx = AnalyzerContext(
        segments=[{"id": "1", "start_sec": 0, "end_sec": 1, "edited_text": "Sample text"}],
        full_text="Sample text",
        whisper_segments_meta=[
            {"start": 0, "end": 1, "text": "Sample text", "compression_ratio": 3.5}
        ],
    )
    result = QualityAnalyzer().analyze(ctx)
    assert any(w.rule_id == "META_COMPRESS" for w in result.warnings)


def test_risk_insufficient_when_clean():
    ctx = AnalyzerContext(
        segments=[{"id": "1", "start_sec": 0, "end_sec": 1, "edited_text": "Clean sample."}],
        full_text="Clean sample.",
        whisper_segments_meta=[],
    )
    result = QualityAnalyzer().analyze(ctx)
    assert result.overall_priority.value in {"INSUFFICIENT_EVIDENCE", "LOW"}


def test_wer_cer_synthetic():
    wer = word_error_rate("one two three", "one two four")
    assert wer["substitutions"] == 1
    cer = character_error_rate("abc", "abd")
    assert cer["reference_chars"] == 3
    assert cer["edits"] >= 1


def _write_silent_wav(path: Path, seconds: float = 0.5, sr: int = 16000) -> None:
    n = int(sr * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * n)


@pytest.mark.asyncio
async def test_quality_api_auth_queue_review_evaluate(client, tmp_path):
    # Unauthenticated
    res = await client.post("/api/jobs/x/quality/analyze")
    assert res.status_code == 401

    await login(client)

    files = {"file": ("SampleAudio.mp3", b"ID3fakeaudio", "audio/mpeg")}
    created = await client.post(
        "/api/jobs",
        files=files,
        data={"model": "small", "language": "English"},
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    for _ in range(50):
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        if job["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.1)
    assert job["status"] == "completed"

    # Inject repetitive text for deterministic warnings
    from app.config import get_settings
    from app.database import Database

    settings = get_settings()
    db = Database(settings.db_path)
    await db.connect()
    segs = await db.list_segments(job_id)
    if segs:
        await db.update_segment_text(
            job_id,
            segs[0]["id"],
            "Hello hello hello hello. This is an example. This is an example.",
            expected_revision=0,
        )
    # Point upload to synthetic wav for optional audio path
    wav = tmp_path / "SampleAudio.wav"
    _write_silent_wav(wav, seconds=1.0)
    await db.update_job(job_id, upload_path=str(wav))
    await db.close()

    enq = await client.post(
        f"/api/jobs/{job_id}/quality/analyze",
        json={"include_audio": False},
    )
    assert enq.status_code == 202, enq.text

    overview = None
    for _ in range(80):
        overview = (await client.get(f"/api/jobs/{job_id}/quality")).json()
        run = overview.get("run") or {}
        if run.get("status") in {"completed", "failed"}:
            break
        await asyncio.sleep(0.15)
    assert overview["run"]["status"] == "completed", overview

    warnings = (await client.get(f"/api/jobs/{job_id}/quality/warnings")).json()["warnings"]
    assert warnings
    assert any(w["rule_id"].startswith("REP_") for w in warnings)

    wid = warnings[0]["id"]
    patched = await client.patch(
        f"/api/jobs/{job_id}/quality/warnings/{wid}",
        json={"review_status": "false_positive", "reviewer_note": "ExampleUser note"},
    )
    assert patched.status_code == 200
    assert patched.json()["review_status"] == "false_positive"

    reopened = await client.patch(
        f"/api/jobs/{job_id}/quality/warnings/{wid}",
        json={"review_status": "open"},
    )
    assert reopened.status_code == 200

    evaluated = await client.post(
        f"/api/jobs/{job_id}/quality/evaluate",
        json={
            "reference_transcript": "Hello. This is an example.",
            "compare_against": "edited",
        },
    )
    assert evaluated.status_code == 200
    assert "wer" in evaluated.json()
    assert "cer" in evaluated.json()

    report = await client.get(f"/api/jobs/{job_id}/quality/report")
    assert report.status_code == 200
    assert "MrBit Quality Analyzer" in report.text

    page = await client.get(f"/quality/{job_id}")
    assert page.status_code == 200
    assert "Quality Analyzer" in page.text


@pytest.mark.asyncio
async def test_quality_worker_restart_marks_running_failed(client):
    await login(client)
    from app.config import get_settings
    from app.database import Database

    settings = get_settings()
    db = Database(settings.db_path)
    await db.connect()
    # Create a dummy completed job row via upload first
    files = {"file": ("SampleAudio.mp3", b"ID3fakeaudio", "audio/mpeg")}
    created = await client.post(
        "/api/jobs", files=files, data={"model": "small", "language": "English"}
    )
    job_id = created.json()["id"]
    for _ in range(50):
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        if job["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.1)

    run = await db.create_quality_run(job_id, include_audio=False)
    await db.conn.execute(
        "UPDATE quality_analysis_runs SET status = 'running' WHERE id = ?",
        (run["id"],),
    )
    await db.conn.commit()
    n = await db.fail_interrupted_quality_runs()
    assert n >= 1
    refreshed = await db.get_quality_run(run["id"])
    await db.close()
    assert refreshed["status"] == "failed"
