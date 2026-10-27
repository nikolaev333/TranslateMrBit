"""Progress percent helpers and job progress fields."""

from __future__ import annotations

import pytest

from app.whisper_engine import DECODE_END, DECODE_START, map_decode_fraction
from tests.conftest import login
from tests.test_jobs import _audio_file, _wait_for_status


def test_map_decode_fraction_bounds():
    assert map_decode_fraction(0) == DECODE_START
    assert map_decode_fraction(1) == DECODE_END
    assert map_decode_fraction(-1) == DECODE_START
    assert map_decode_fraction(2) == DECODE_END
    mid = map_decode_fraction(0.5)
    assert DECODE_START < mid < DECODE_END


@pytest.mark.asyncio
async def test_job_exposes_progress_fields(client):
    await login(client)
    res = await client.post(
        "/api/jobs",
        files={"file": _audio_file()},
        data={"model": "small", "language": "Bulgarian"},
    )
    assert res.status_code == 201
    created = res.json()
    job = await _wait_for_status(client, created["id"], {"completed", "failed"})
    assert job["status"] == "completed"
    assert job["progress_percent"] == 100.0
    assert "progress_percent" in job
    assert "cpu_percent" in job
