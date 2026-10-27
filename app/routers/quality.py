"""MrBit Quality Analyzer API."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.config import Settings, get_settings
from app.database import Database
from app.models import JobStatus
from app.services.transcript_segments import join_segment_texts
from app.services.wer import character_error_rate, word_error_rate

router = APIRouter(
    prefix="/api/jobs",
    tags=["quality"],
    dependencies=[Depends(require_auth)],
)


def get_db(request: Request) -> Database:
    return request.app.state.db


class AnalyzeRequest(BaseModel):
    include_audio: bool | None = None


class WarningPatchRequest(BaseModel):
    review_status: Literal["open", "reviewed", "confirmed_error", "false_positive"]
    reviewer_note: str | None = Field(default=None, max_length=2000)


class EvaluateRequest(BaseModel):
    reference_transcript: str = Field(min_length=1, max_length=500_000)
    compare_against: Literal["original", "edited", "reviewed"] = "edited"


async def _require_completed_job(db: Database, job_id: str) -> dict[str, Any]:
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transcription is not completed yet",
        )
    return job


def _warning_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "job_id": row["job_id"],
        "rule_id": row["rule_id"],
        "description": row["description"],
        "priority": row["priority"],
        "explanation": row["explanation"],
        "evidence": row.get("evidence") or {},
        "segment_ids": row.get("segment_ids") or [],
        "start_sec": row.get("start_sec"),
        "end_sec": row.get("end_sec"),
        "review_status": row.get("review_status") or "open",
        "reviewer_note": row.get("reviewer_note"),
        "reviewed_at": row.get("reviewed_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _run_public(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run["id"],
        "job_id": run["job_id"],
        "status": run["status"],
        "analysis_version": run.get("analysis_version"),
        "include_audio": bool(run.get("include_audio", 1)),
        "overall_priority": run.get("overall_priority"),
        "segments_analyzed": run.get("segments_analyzed") or 0,
        "warnings_count": run.get("warnings_count") or 0,
        "error_message": run.get("error_message"),
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "updated_at": run.get("updated_at"),
    }


@router.post("/{job_id}/quality/analyze", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_quality_analysis(
    job_id: str,
    request: Request,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    payload: AnalyzeRequest | None = None,
):
    await _require_completed_job(db, job_id)
    include_audio = (
        settings.quality_include_audio
        if payload is None or payload.include_audio is None
        else bool(payload.include_audio)
    )
    # Avoid stacking identical queued runs
    latest = await db.get_latest_quality_run(job_id)
    if latest and latest.get("status") in {"queued", "running"}:
        return {
            "message": "Analysis already in progress",
            "run": _run_public(latest),
        }
    run = await db.create_quality_run(job_id, include_audio=include_audio)
    worker = getattr(request.app.state, "quality_worker", None)
    if worker is not None:
        worker.notify()
    return {"message": "Quality analysis queued", "run": _run_public(run)}


@router.get("/{job_id}/quality")
async def get_quality_overview(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    await _require_completed_job(db, job_id)
    run = await db.get_latest_quality_run(job_id)
    if not run:
        return {
            "job_id": job_id,
            "run": None,
            "summary": {
                "segments_analyzed": 0,
                "warnings_total": 0,
                "open_reviews": 0,
                "confirmed_errors": 0,
                "false_positives": 0,
                "reviewed": 0,
                "by_rule": {},
                "by_priority": {},
            },
        }
    warnings = await db.list_quality_warnings(job_id, run_id=run["id"])
    by_rule: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    open_n = confirmed = false_pos = reviewed = 0
    for w in warnings:
        by_rule[w["rule_id"]] = by_rule.get(w["rule_id"], 0) + 1
        by_priority[w["priority"]] = by_priority.get(w["priority"], 0) + 1
        st = w.get("review_status") or "open"
        if st == "open":
            open_n += 1
        elif st == "confirmed_error":
            confirmed += 1
        elif st == "false_positive":
            false_pos += 1
        elif st == "reviewed":
            reviewed += 1
    return {
        "job_id": job_id,
        "run": _run_public(run),
        "summary": {
            "segments_analyzed": run.get("segments_analyzed") or 0,
            "warnings_total": len(warnings),
            "open_reviews": open_n,
            "confirmed_errors": confirmed,
            "false_positives": false_pos,
            "reviewed": reviewed,
            "by_rule": by_rule,
            "by_priority": by_priority,
            "overall_priority": run.get("overall_priority"),
            "disclaimer": (
                "Warnings are heuristic review indicators, not proof of transcription errors. "
                "No accuracy percentage is invented from model metadata."
            ),
        },
    }


@router.get("/{job_id}/quality/warnings")
async def list_warnings(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
    review_status: Annotated[str | None, Query()] = None,
):
    await _require_completed_job(db, job_id)
    run = await db.get_latest_quality_run(job_id)
    if not run:
        return {"job_id": job_id, "run_id": None, "warnings": []}
    rows = await db.list_quality_warnings(
        job_id, run_id=run["id"], review_status=review_status
    )
    return {
        "job_id": job_id,
        "run_id": run["id"],
        "warnings": [_warning_public(r) for r in rows],
    }


@router.patch("/{job_id}/quality/warnings/{warning_id}")
async def patch_warning(
    job_id: str,
    warning_id: str,
    payload: WarningPatchRequest,
    db: Annotated[Database, Depends(get_db)],
):
    await _require_completed_job(db, job_id)
    updated = await db.update_quality_warning_review(
        job_id,
        warning_id,
        review_status=payload.review_status,
        reviewer_note=payload.reviewer_note,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Warning not found")
    return _warning_public(updated)


@router.get("/{job_id}/quality/report")
async def quality_report(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
    format: Annotated[Literal["json"], Query()] = "json",
):
    """Downloadable analysis report (JSON). PDF optional later — not claimed here."""
    del format  # only json for now
    job = await _require_completed_job(db, job_id)
    run = await db.get_latest_quality_run(job_id)
    if not run or run.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Completed quality analysis not available yet",
        )
    warnings = await db.list_quality_warnings(job_id, run_id=run["id"])
    evaluations = await db.list_reference_evaluations(job_id)
    report = {
        "product": "TranslateMrBit",
        "module": "MrBit Quality Analyzer",
        "job_id": job_id,
        "original_filename": job.get("original_filename"),
        "disclaimer": (
            "Heuristic warnings are not verified errors. "
            "WER/CER appear only when a human reference transcript was provided."
        ),
        "run": _run_public(run),
        "config": json.loads(run.get("config_json") or "{}"),
        "score_breakdown": (json.loads(run.get("result_json") or "{}")).get(
            "score_breakdown"
        ),
        "warnings": [_warning_public(w) for w in warnings],
        "reference_evaluations": [
            {
                "id": e["id"],
                "compare_against": e["compare_against"],
                "wer": e.get("wer"),
                "cer": e.get("cer"),
                "substitutions": e.get("substitutions"),
                "deletions": e.get("deletions"),
                "insertions": e.get("insertions"),
                "created_at": e.get("created_at"),
            }
            for e in evaluations
        ],
    }
    body = json.dumps(report, ensure_ascii=False, indent=2)
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="quality-report-{job_id[:8]}.json"'
        },
    )


@router.post("/{job_id}/quality/evaluate")
async def evaluate_with_reference(
    job_id: str,
    payload: EvaluateRequest,
    db: Annotated[Database, Depends(get_db)],
):
    job = await _require_completed_job(db, job_id)
    if payload.compare_against == "original":
        hypothesis = job.get("transcription_text") or ""
    elif payload.compare_against == "reviewed":
        hypothesis = job.get("reviewed_text")
        if hypothesis is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No reviewed transcript available",
            )
    else:
        segments = await db.list_segments(job_id)
        if segments:
            hypothesis = join_segment_texts(segments, use_edited=True)
        else:
            hypothesis = job.get("reviewed_text") or job.get("transcription_text") or ""

    wer = word_error_rate(payload.reference_transcript, hypothesis)
    cer = character_error_rate(payload.reference_transcript, hypothesis)
    run = await db.get_latest_quality_run(job_id)
    saved = await db.save_reference_evaluation(
        {
            "job_id": job_id,
            "run_id": run["id"] if run else None,
            "compare_against": payload.compare_against,
            "reference_text": payload.reference_transcript,
            "hypothesis_text": hypothesis,
            "wer": wer["wer"],
            "cer": cer["cer"],
            "substitutions": wer["substitutions"],
            "deletions": wer["deletions"],
            "insertions": wer["insertions"],
            "reference_words": wer["reference_words"],
            "hypothesis_words": wer["hypothesis_words"],
        }
    )
    return {
        "job_id": job_id,
        "evaluation_id": saved["id"],
        "compare_against": payload.compare_against,
        "wer": wer["wer"],
        "cer": cer["cer"],
        "substitutions": wer["substitutions"],
        "deletions": wer["deletions"],
        "insertions": wer["insertions"],
        "reference_words": wer["reference_words"],
        "hypothesis_words": wer["hypothesis_words"],
        "reference_chars": cer["reference_chars"],
        "hypothesis_chars": cer["hypothesis_chars"],
        "note": "Measured against the provided human reference only.",
    }
