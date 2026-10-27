"""Unit and integration tests for MrBit Smart Vocabulary."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.smart_vocabulary import (
    VocabularyTerm,
    analyze_transcription,
    build_initial_prompt,
    build_reviewed_text,
    find_matches,
)
from app.services.wer import word_error_rate
from tests.conftest import login


def _terms() -> list[VocabularyTerm]:
    return [
        VocabularyTerm(
            id="1",
            correct="RTP",
            alternatives=("ар ти пи", "R T P", "артипи"),
            language="Bulgarian",
            category="iGaming",
            description="",
            enabled=True,
        ),
        VocabularyTerm(
            id="2",
            correct="Jackpot",
            alternatives=("джак пот", "джакпот"),
            language="Bulgarian",
            category="iGaming",
            description="",
            enabled=True,
        ),
        VocabularyTerm(
            id="3",
            correct="Bet Limits",
            alternatives=("бет лимитс", "бет лимити"),
            language="Bulgarian",
            category="Technical",
            description="",
            enabled=True,
        ),
        VocabularyTerm(
            id="4",
            correct="DisabledTerm",
            alternatives=("disabled alt",),
            language="English",
            category="Test",
            description="",
            enabled=False,
        ),
    ]


def test_case_insensitive_and_phrase_matching():
    text = "We need to check the R T P configuration and the джак пот."
    matches = find_matches(text, _terms(), language="Bulgarian")
    corrected = {m.correct for m in matches}
    assert "RTP" in corrected
    assert "Jackpot" in corrected
    result = analyze_transcription(text, _terms(), language="Bulgarian")
    assert "RTP" in result.suggested_text
    assert "Jackpot" in result.suggested_text
    assert "R T P" not in result.suggested_text


def test_bulgarian_unicode_multiword():
    text = "Моля проверете бет лимити преди пускане."
    result = analyze_transcription(text, _terms(), language="Bulgarian")
    assert "Bet Limits" in result.suggested_text
    assert result.original_text == text


def test_false_positive_prevention_short_and_disabled():
    text = "The letter R is fine and disabled alt should stay."
    result = analyze_transcription(text, _terms(), language="English")
    # Disabled entry must not match; bare "R" is too short to be an alternative.
    assert result.suggested_text == text
    assert result.matches == []


def test_overlapping_prefers_longest():
    terms = [
        VocabularyTerm(
            id="a",
            correct="Bet Limits",
            alternatives=("bet limmits", "bet"),
            language="English",
            category="Technical",
            description="",
            enabled=True,
        ),
    ]
    text = "Check bet limmits now"
    matches = find_matches(text, terms, language="English")
    assert len(matches) == 1
    assert matches[0].correct == "Bet Limits"
    assert matches[0].matched_text.lower() == "bet limmits"

    # Already-correct phrase occupies the span so short alt "bet" cannot fire.
    identity = find_matches("Check bet limits now", terms, language="English")
    assert identity == []


def test_original_preserved_and_decisions():
    original = "Check the R T P value."
    analysis = analyze_transcription(original, _terms(), language="English")
    assert analysis.original_text == original
    reviewed = build_reviewed_text(
        original,
        [
            {
                "entry_id": m.entry_id,
                "correct": m.correct,
                "matched_text": m.matched_text,
                "start": m.start,
                "end": m.end,
            }
            for m in analysis.matches
        ],
        [{"match_index": 0, "action": "accept"}],
    )
    assert "RTP" in reviewed
    rejected = build_reviewed_text(
        original,
        [
            {
                "entry_id": m.entry_id,
                "correct": m.correct,
                "matched_text": m.matched_text,
                "start": m.start,
                "end": m.end,
            }
            for m in analysis.matches
        ],
        [{"match_index": 0, "action": "reject"}],
    )
    assert rejected == original


def test_initial_prompt_budget():
    prompt = build_initial_prompt(_terms(), language="Bulgarian", max_chars=80)
    assert prompt is not None
    assert prompt.startswith("Domain terminology:")
    assert len(prompt) <= 80 + 20  # small slack for punctuation assembly
    assert "RTP" in prompt


def test_wer_measurement():
    metrics = word_error_rate("one two three", "one two four")
    assert metrics["reference_words"] == 3
    assert metrics["substitutions"] == 1
    assert metrics["wer"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_vocabulary_crud_auth_and_persistence(client, tmp_path):
    # Unauthenticated
    res = await client.get("/api/vocabulary")
    assert res.status_code == 401

    await login(client)

    create = await client.post(
        "/api/vocabulary",
        json={
            "correct_term": "RTP",
            "alternatives": ["R T P", "ар ти пи"],
            "language": "Bulgarian",
            "category": "iGaming",
            "description": "Return to Player",
            "enabled": True,
        },
    )
    assert create.status_code == 201, create.text
    entry = create.json()
    entry_id = entry["id"]
    assert entry["correct_term"] == "RTP"

    listed = await client.get("/api/vocabulary?search=RTP")
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1

    updated = await client.put(
        f"/api/vocabulary/{entry_id}",
        json={"enabled": False, "alternatives": ["R T P"]},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    # Persistence across reconnect using same DB path from settings
    from app.config import get_settings
    from app.database import Database

    settings = get_settings()
    db = Database(settings.db_path)
    await db.connect()
    row = await db.get_vocabulary_entry(entry_id)
    await db.close()
    assert row is not None
    assert row["correct_term"] == "RTP"
    assert row["enabled"] is False

    deleted = await client.delete(f"/api/vocabulary/{entry_id}")
    assert deleted.status_code == 200
    missing = await client.get(f"/api/vocabulary/{entry_id}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_analyze_corrections_and_original_preserved(client):
    await login(client)

    await client.post(
        "/api/vocabulary",
        json={
            "correct_term": "RTP",
            "alternatives": ["R T P"],
            "language": "English",
            "category": "iGaming",
            "enabled": True,
        },
    )

    # Create a completed job via upload + wait for fake worker
    audio = Path(__file__).resolve().parent / "fixtures"
    # Use tiny bytes accepted by fake probe
    files = {"file": ("sample.mp3", b"ID3fakeaudio", "audio/mpeg")}
    data = {"model": "small", "language": "English"}
    created = await client.post("/api/jobs", files=files, data=data)
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    # Wait for completion
    job = None
    for _ in range(50):
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        if job["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.1)
    assert job["status"] == "completed"

    # Inject transcription text that contains a vocabulary misspelling
    from app.config import get_settings
    from app.database import Database

    settings = get_settings()
    db = Database(settings.db_path)
    await db.connect()
    original = "We need to check the R T P configuration."
    await db.update_job(job_id, transcription_text=original)
    await db.close()

    analysis = await client.post(f"/api/jobs/{job_id}/analyze")
    assert analysis.status_code == 200, analysis.text
    body = analysis.json()
    assert body["original_text"] == original
    assert "RTP" in body["suggested_text"]
    assert body["matches"]

    corrections = await client.post(
        f"/api/jobs/{job_id}/corrections",
        json={
            "decisions": [{"match_index": 0, "action": "accept"}],
        },
    )
    assert corrections.status_code == 200
    corr = corrections.json()
    assert corr["original_text"] == original
    assert "RTP" in corr["reviewed_text"]
    assert corr["suggestions_accepted"] == 1

    # Original Whisper text still intact
    result = await client.get(f"/api/jobs/{job_id}/result")
    assert result.json()["transcription_text"] == original
    assert result.json()["reviewed_text"] == corr["reviewed_text"]

    evaluated = await client.post(
        f"/api/jobs/{job_id}/evaluate",
        json={
            "reference_transcript": "We need to check the RTP configuration.",
            "compare_against": "reviewed",
        },
    )
    assert evaluated.status_code == 200
    assert evaluated.json()["wer"] == 0.0

    # Restore original reviewed state
    restored = await client.post(
        f"/api/jobs/{job_id}/corrections",
        json={"restore_original": True, "decisions": []},
    )
    assert restored.status_code == 200
    result2 = await client.get(f"/api/jobs/{job_id}/result")
    assert result2.json()["transcription_text"] == original
    assert result2.json()["reviewed_text"] is None


@pytest.mark.asyncio
async def test_vocabulary_page_requires_auth(client):
    res = await client.get("/vocabulary", follow_redirects=False)
    assert res.status_code in {303, 401, 302}
    await login(client)
    page = await client.get("/vocabulary")
    assert page.status_code == 200
    assert "Smart Vocabulary" in page.text
