"""Transcription job API endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, PlainTextResponse

from app.auth import require_auth
from app.config import SUPPORTED_LANGUAGES, SUPPORTED_MODELS, Settings, get_settings
from app.database import Database
from app.media import delete_job_files, save_upload
from app.models import JobStatus
from app.schemas import (
    CorrectionsRequest,
    CorrectionsResponse,
    EvaluateRequest,
    EvaluateResponse,
    JobCreateResponse,
    JobListResponse,
    JobResponse,
    JobResultResponse,
    MessageResponse,
    OptionsResponse,
    VocabularyAnalysisResponse,
    VocabularyMatchResponse,
    job_row_to_response,
)
from app.services.smart_vocabulary import (
    analysis_to_dict,
    analyze_transcription,
    build_reviewed_text,
    term_from_row,
)
from app.services.wer import word_error_rate

router = APIRouter(
    prefix="/api",
    tags=["jobs"],
    dependencies=[Depends(require_auth)],
)


def get_db(request: Request) -> Database:
    return request.app.state.db


@router.get("/options", response_model=OptionsResponse)
async def options(settings: Annotated[Settings, Depends(get_settings)]) -> OptionsResponse:
    return OptionsResponse(
        models=list(SUPPORTED_MODELS),
        languages=list(SUPPORTED_LANGUAGES),
        default_model=settings.whisper_model,
        default_language=settings.whisper_language,
        max_upload_mb=settings.max_upload_mb,
        allowed_extensions=sorted(settings.allowed_extension_set),
    )


@router.post("/jobs", response_model=JobCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Database, Depends(get_db)],
    file: Annotated[UploadFile, File(...)],
    model: Annotated[str, Form()] = "large",
    language: Annotated[str, Form()] = "Bulgarian",
    inference_backend: Annotated[str, Form()] = "",
    priority: Annotated[int, Form()] = 100,
    allow_unsafe_model: Annotated[str, Form()] = "false",
):
    from app.auth import rate_limiter

    client_host = request.client.host if request.client else "unknown"
    if not rate_limiter.is_allowed(
        f"job-submit:{client_host}",
        getattr(settings, "job_submit_rate_limit", 30),
        getattr(settings, "job_submit_rate_window_seconds", 60),
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many job submissions. Please wait and try again.",
        )

    model_name = (model or settings.whisper_model).strip()
    language_name = (language or settings.whisper_language).strip() or "Bulgarian"
    backend_name = (inference_backend or settings.inference_backend or "openai-whisper").strip()
    allow_unsafe = str(allow_unsafe_model).strip().lower() in {"1", "true", "yes", "on"}
    try:
        priority_val = max(0, min(1000, int(priority)))
    except (TypeError, ValueError):
        priority_val = 100

    # Do not silently substitute a different model — only reject unknown names
    # outside the known Whisper set (including large-v* aliases).
    known = set(SUPPORTED_MODELS) | {
        "tiny.en",
        "base.en",
        "small.en",
        "medium.en",
        "large-v1",
        "large-v2",
        "large-v3",
    }
    if model_name not in known:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported model '{model_name}'",
        )

    if getattr(settings, "smart_processing_enabled", True):
        from app.services.smart_processing.model_recommender import validate_model_choice
        from app.services.smart_processing.resource_monitor import collect_resources

        resources = collect_resources(
            upload_dir=settings.upload_dir,
            output_dir=settings.output_dir,
            model_dir=settings.whisper_model_dir,
        )
        verdict = validate_model_choice(
            model_name,
            device=settings.whisper_device,
            backend=backend_name,
            safety_margin_gb=settings.processing_safety_margin_gb,
            allow_unsafe=allow_unsafe or settings.processing_allow_unsafe_models,
            resources=resources,
        )
        if not verdict.get("ok"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": verdict.get("reason") or "Model rejected by resource policy",
                    "code": verdict.get("code"),
                    "model_id": model_name,
                    "recommendation": verdict.get("recommendation"),
                    "hint": "Pass allow_unsafe_model=true to confirm an unsafe selection explicitly.",
                },
            )

    job_id, original, dest, size, content_type = await save_upload(file, settings)

    job = await db.create_job(
        {
            "id": job_id,
            "original_filename": original,
            "stored_filename": dest.name,
            "upload_path": str(dest),
            "file_size": size,
            "content_type": content_type,
            "model_name": model_name,
            "language": language_name,
            "task": "transcribe",
            "inference_backend": backend_name,
            "priority": priority_val,
            "allow_unsafe_model": allow_unsafe or settings.processing_allow_unsafe_models,
            "max_retries": settings.processing_max_retries,
        }
    )
    return JobCreateResponse(
        id=job["id"],
        status=job["status"],
        original_filename=job["original_filename"],
        model_name=job["model_name"],
        language=job["language"],
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    db: Annotated[Database, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    jobs = await db.list_jobs(limit=limit, offset=offset)
    total = await db.count_jobs()
    return JobListResponse(
        jobs=[job_row_to_response(j) for j in jobs],
        total=total,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job_row_to_response(job)


@router.get("/jobs/{job_id}/result", response_model=JobResultResponse)
async def get_result(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    formats: list[str] = []
    for fmt, key in (
        ("txt", "output_txt_path"),
        ("srt", "output_srt_path"),
        ("vtt", "output_vtt_path"),
        ("json", "output_json_path"),
    ):
        path = job.get(key)
        if path and Path(path).is_file():
            formats.append(fmt)

    if job["status"] != JobStatus.COMPLETED.value:
        return JobResultResponse(
            id=job["id"],
            status=job["status"],
            transcription_text=None,
            reviewed_text=job.get("reviewed_text"),
            detected_language=job.get("detected_language"),
            duration_seconds=job.get("duration_seconds"),
            available_formats=formats,
            has_vocabulary_analysis=bool(job.get("vocabulary_analysis_json")),
            suggestions_accepted=int(job.get("suggestions_accepted") or 0),
            suggestions_rejected=int(job.get("suggestions_rejected") or 0),
        )

    return JobResultResponse(
        id=job["id"],
        status=job["status"],
        transcription_text=job.get("transcription_text"),
        reviewed_text=job.get("reviewed_text"),
        detected_language=job.get("detected_language"),
        duration_seconds=job.get("duration_seconds"),
        available_formats=formats,
        has_vocabulary_analysis=bool(job.get("vocabulary_analysis_json")),
        suggestions_accepted=int(job.get("suggestions_accepted") or 0),
        suggestions_rejected=int(job.get("suggestions_rejected") or 0),
    )


def _parse_analysis(job: dict) -> dict | None:
    raw = job.get("vocabulary_analysis_json")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


@router.post("/jobs/{job_id}/analyze", response_model=VocabularyAnalysisResponse)
async def analyze_job_vocabulary(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    """Run (or refresh) MrBit Smart Vocabulary analysis. Does not alter original text."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transcription is not completed yet",
        )
    text = job.get("transcription_text") or ""
    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job has no transcription text to analyze",
        )

    rows = await db.list_vocabulary_entries(enabled=True, limit=1000, offset=0)
    terms = [term_from_row(r) for r in rows]
    language = job.get("detected_language") or job.get("language")
    analysis = analyze_transcription(text, terms, language=language)
    payload = analysis_to_dict(analysis)
    await db.update_job(
        job_id,
        vocabulary_analysis_json=json.dumps(payload, ensure_ascii=False),
    )
    return VocabularyAnalysisResponse(
        job_id=job_id,
        original_text=payload["original_text"],
        suggested_text=payload["suggested_text"],
        matches=[VocabularyMatchResponse(**m) for m in payload["matches"]],
        reviewed_text=job.get("reviewed_text"),
        vocabulary_prompt_used=job.get("vocabulary_prompt_used"),
    )


@router.get("/jobs/{job_id}/analyze", response_model=VocabularyAnalysisResponse)
async def get_job_vocabulary_analysis(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    data = _parse_analysis(job)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No vocabulary analysis yet. POST /analyze to generate one.",
        )
    return VocabularyAnalysisResponse(
        job_id=job_id,
        original_text=data.get("original_text") or job.get("transcription_text") or "",
        suggested_text=data.get("suggested_text") or "",
        matches=[VocabularyMatchResponse(**m) for m in (data.get("matches") or [])],
        reviewed_text=job.get("reviewed_text"),
        vocabulary_prompt_used=job.get("vocabulary_prompt_used"),
    )


@router.post("/jobs/{job_id}/corrections", response_model=CorrectionsResponse)
async def apply_job_corrections(
    job_id: str,
    payload: CorrectionsRequest,
    db: Annotated[Database, Depends(get_db)],
):
    """Save a reviewed transcription. Original Whisper text is never overwritten."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transcription is not completed yet",
        )

    original = job.get("transcription_text") or ""
    if payload.restore_original:
        await db.update_job(
            job_id,
            reviewed_text=None,
            suggestions_accepted=0,
            suggestions_rejected=0,
        )
        return CorrectionsResponse(
            job_id=job_id,
            original_text=original,
            reviewed_text=original,
            suggestions_accepted=0,
            suggestions_rejected=0,
        )

    accepted = rejected = 0
    for decision in payload.decisions:
        action = decision.action.lower()
        if action in {"accept", "edit"}:
            accepted += 1
        else:
            rejected += 1

    if payload.reviewed_text is not None:
        reviewed = payload.reviewed_text
    else:
        analysis = _parse_analysis(job) or {}
        matches = analysis.get("matches") or []
        reviewed = build_reviewed_text(
            original,
            matches,
            [d.model_dump() for d in payload.decisions],
        )

    await db.update_job(
        job_id,
        reviewed_text=reviewed,
        suggestions_accepted=accepted,
        suggestions_rejected=rejected,
    )
    # Persist reviewed TXT alongside original outputs (separate filename).
    out_dir = Path(job.get("output_txt_path") or "").parent
    if out_dir and out_dir.is_dir():
        reviewed_path = out_dir / f"{job_id}.reviewed.txt"
        try:
            reviewed_path.write_text(reviewed, encoding="utf-8")
        except OSError:
            pass

    return CorrectionsResponse(
        job_id=job_id,
        original_text=original,
        reviewed_text=reviewed,
        suggestions_accepted=accepted,
        suggestions_rejected=rejected,
    )


@router.get("/jobs/{job_id}/download-reviewed")
async def download_reviewed(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    text = job.get("reviewed_text")
    if text is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No reviewed transcription saved yet",
        )
    stem = Path(job["original_filename"]).stem
    return PlainTextResponse(
        text,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}.reviewed.txt"'
        },
    )


@router.post("/jobs/{job_id}/evaluate", response_model=EvaluateResponse)
async def evaluate_job(
    job_id: str,
    payload: EvaluateRequest,
    db: Annotated[Database, Depends(get_db)],
):
    """Optional WER against a human reference. Reports measured values only."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transcription is not completed yet",
        )

    if payload.compare_against == "reviewed":
        hypothesis = job.get("reviewed_text")
        if hypothesis is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No reviewed transcription available; compare against original or save corrections first",
            )
    else:
        hypothesis = job.get("transcription_text") or ""

    metrics = word_error_rate(payload.reference_transcript, hypothesis)
    await db.update_job(job_id, reference_transcript=payload.reference_transcript)
    return EvaluateResponse(
        job_id=job_id,
        compare_against=payload.compare_against,
        hypothesis_text=hypothesis,
        reference_transcript=payload.reference_transcript,
        wer=metrics["wer"],
        reference_words=metrics["reference_words"],
        hypothesis_words=metrics["hypothesis_words"],
        substitutions=metrics["substitutions"],
        deletions=metrics["deletions"],
        insertions=metrics["insertions"],
        edits=metrics["edits"],
        suggestions_accepted=int(job.get("suggestions_accepted") or 0),
        suggestions_rejected=int(job.get("suggestions_rejected") or 0),
    )


@router.get("/jobs/{job_id}/download")
async def download_result(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
    format: Annotated[Literal["txt", "srt", "vtt", "json"], Query()] = "txt",
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transcription is not completed yet",
        )

    key_map = {
        "txt": "output_txt_path",
        "srt": "output_srt_path",
        "vtt": "output_vtt_path",
        "json": "output_json_path",
    }
    path_str = job.get(key_map[format])
    if not path_str or not Path(path_str).is_file():
        # Fallback: generate txt from DB text if file missing
        if format == "txt" and job.get("transcription_text"):
            tmp = NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".txt",
                delete=False,
            )
            tmp.write(job["transcription_text"])
            tmp.close()
            stem = Path(job["original_filename"]).stem
            return FileResponse(
                tmp.name,
                media_type="text/plain; charset=utf-8",
                filename=f"{stem}.txt",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Format '{format}' is not available for this job",
        )

    media_types = {
        "txt": "text/plain; charset=utf-8",
        "srt": "application/x-subrip; charset=utf-8",
        "vtt": "text/vtt; charset=utf-8",
        "json": "application/json; charset=utf-8",
    }
    stem = Path(job["original_filename"]).stem
    return FileResponse(
        path_str,
        media_type=media_types[format],
        filename=f"{stem}.{format}",
    )


@router.delete("/jobs/{job_id}", response_model=MessageResponse)
async def delete_job(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job["status"] == JobStatus.PROCESSING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a job while it is processing",
        )
    deleted = await db.delete_job(job_id)
    if deleted:
        delete_job_files(deleted)
        # Also remove per-job output directory if empty/leftover
        out_dir = Path(deleted.get("output_txt_path") or "").parent
        if out_dir and out_dir.exists() and out_dir.is_dir():
            try:
                for child in out_dir.iterdir():
                    child.unlink(missing_ok=True)
                out_dir.rmdir()
            except OSError:
                pass
    return MessageResponse(message="Job deleted")
