"""Transcription job API endpoints."""

from __future__ import annotations

from pathlib import Path
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
from fastapi.responses import FileResponse

from app.auth import require_auth
from app.config import SUPPORTED_LANGUAGES, SUPPORTED_MODELS, Settings, get_settings
from app.database import Database
from app.media import delete_job_files, save_upload
from app.models import JobStatus
from app.schemas import (
    JobCreateResponse,
    JobListResponse,
    JobResponse,
    JobResultResponse,
    MessageResponse,
    OptionsResponse,
    job_row_to_response,
)

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
):
    model_name = (model or settings.whisper_model).strip()
    language_name = (language or settings.whisper_language).strip() or "Bulgarian"

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
            detected_language=job.get("detected_language"),
            duration_seconds=job.get("duration_seconds"),
            available_formats=formats,
        )

    return JobResultResponse(
        id=job["id"],
        status=job["status"],
        transcription_text=job.get("transcription_text"),
        detected_language=job.get("detected_language"),
        duration_seconds=job.get("duration_seconds"),
        available_formats=formats,
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
            from tempfile import NamedTemporaryFile

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
