"""MrBit Smart Processing Engine API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.config import Settings, get_settings
from app.database import Database
from app.inference.backend_registry import list_backends
from app.inference.model_profiles import list_model_profiles
from app.model_disk import list_model_download_status
from app.services.smart_processing.model_recommender import (
    recommend_models,
    validate_model_choice,
)
from app.services.smart_processing.resource_monitor import collect_resources

router = APIRouter(tags=["processing"], dependencies=[Depends(require_auth)])


def get_db(request: Request) -> Database:
    return request.app.state.db


class CancelResponse(BaseModel):
    message: str
    job: dict[str, Any]


class RetryResponse(BaseModel):
    message: str
    job: dict[str, Any]


@router.get("/api/system/resources")
async def system_resources(
    settings: Annotated[Settings, Depends(get_settings)],
):
    return collect_resources(
        upload_dir=settings.upload_dir,
        output_dir=settings.output_dir,
        model_dir=settings.whisper_model_dir,
    )


@router.get("/api/system/workers")
async def system_workers(db: Annotated[Database, Depends(get_db)]):
    return {"workers": await db.list_worker_heartbeats()}


@router.get("/api/processing/queue")
async def processing_queue(
    db: Annotated[Database, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    jobs = await db.list_queue_jobs(limit=limit)
    active = await db.count_active_jobs()
    return {
        "active_count": active,
        "jobs": [
            {
                "id": j["id"],
                "original_filename": j.get("original_filename"),
                "model_name": j.get("model_name"),
                "inference_backend": j.get("inference_backend") or "openai-whisper",
                "priority": j.get("priority") or 100,
                "status": j.get("status"),
                "processing_stage": j.get("processing_stage"),
                "retry_count": j.get("retry_count") or 0,
                "cancel_requested": bool(j.get("cancel_requested")),
                "progress_message": j.get("progress_message"),
                "created_at": j.get("created_at"),
                "started_at": j.get("started_at"),
                "error_message": j.get("error_message"),
            }
            for j in jobs
        ],
    }


@router.get("/api/processing/jobs/{job_id}")
async def processing_job_detail(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {
        "id": job["id"],
        "original_filename": job.get("original_filename"),
        "status": job.get("status"),
        "processing_stage": job.get("processing_stage"),
        "model_name": job.get("model_name"),
        "inference_backend": job.get("inference_backend"),
        "priority": job.get("priority"),
        "retry_count": job.get("retry_count"),
        "max_retries": job.get("max_retries"),
        "cancel_requested": bool(job.get("cancel_requested")),
        "queue_wait_seconds": job.get("queue_wait_seconds"),
        "model_load_seconds": job.get("model_load_seconds"),
        "inference_seconds": job.get("inference_seconds"),
        "finalize_seconds": job.get("finalize_seconds"),
        "realtime_factor": job.get("realtime_factor"),
        "peak_memory_bytes": job.get("peak_memory_bytes"),
        "duration_seconds": job.get("duration_seconds"),
        "progress_message": job.get("progress_message"),
        "error_message": job.get("error_message"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
    }


@router.post("/api/processing/jobs/{job_id}/cancel", response_model=CancelResponse)
async def cancel_job(job_id: str, db: Annotated[Database, Depends(get_db)]):
    job = await db.request_job_cancel(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return CancelResponse(message="Cancellation recorded", job=job)


@router.post("/api/processing/jobs/{job_id}/retry", response_model=RetryResponse)
async def retry_job(job_id: str, db: Annotated[Database, Depends(get_db)]):
    try:
        job = await db.retry_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return RetryResponse(message="Job re-queued", job=job)


@router.get("/api/models")
async def models_catalog(
    settings: Annotated[Settings, Depends(get_settings)],
):
    from app.config import SUPPORTED_MODELS

    disk = list_model_download_status(settings.whisper_model_dir, SUPPORTED_MODELS)
    installed = {d["name"] for d in disk if d.get("downloaded")}
    return {
        "profiles": list_model_profiles(),
        "backends": list_backends(settings),
        "disk_status": disk,
        "installed": sorted(installed),
        "default_model": settings.whisper_model,
        "default_backend": settings.inference_backend,
    }


class DownloadRequest(BaseModel):
    models: list[str] = Field(default_factory=list)


@router.post("/api/models/download")
async def models_download(
    request: Request,
    body: DownloadRequest | None = None,
):
    """Queue model weight download in the worker/preloader — never blocks /health."""
    from app.config import SUPPORTED_MODELS

    preloader = request.app.state.model_preloader
    already_running = preloader.snapshot().get("running")
    names = (body.models if body and body.models else None) or list(SUPPORTED_MODELS)
    try:
        status_payload = preloader.start(names)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    message = (
        "Model download already in progress"
        if already_running
        else "Model download queued (weights only; not loaded into API RAM)"
    )
    return {"message": message, "status": status_payload}


@router.get("/api/models/recommendations")
async def models_recommendations(
    settings: Annotated[Settings, Depends(get_settings)],
    device: Annotated[str | None, Query()] = None,
    backend: Annotated[str | None, Query()] = None,
):
    resources = collect_resources(
        upload_dir=settings.upload_dir,
        output_dir=settings.output_dir,
        model_dir=settings.whisper_model_dir,
    )
    from app.config import SUPPORTED_MODELS

    disk = list_model_download_status(settings.whisper_model_dir, SUPPORTED_MODELS)
    installed = {d["name"] for d in disk if d.get("downloaded")}
    return recommend_models(
        device=device or settings.whisper_device,
        backend=backend or settings.inference_backend,
        safety_margin_gb=settings.processing_safety_margin_gb,
        resources=resources,
        installed_models=installed,
    )


@router.post("/api/models/validate")
async def validate_model(
    settings: Annotated[Settings, Depends(get_settings)],
    model: Annotated[str, Query()],
    allow_unsafe: Annotated[bool, Query()] = False,
):
    resources = collect_resources(
        upload_dir=settings.upload_dir,
        output_dir=settings.output_dir,
        model_dir=settings.whisper_model_dir,
    )
    return validate_model_choice(
        model,
        device=settings.whisper_device,
        backend=settings.inference_backend,
        safety_margin_gb=settings.processing_safety_margin_gb,
        allow_unsafe=allow_unsafe or settings.processing_allow_unsafe_models,
        resources=resources,
    )


@router.get("/api/processing/metrics")
async def processing_metrics(
    db: Annotated[Database, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    rows = await db.list_processing_metrics(limit=limit)
    return {
        "metrics": rows,
        "note": "Values are measured from completed jobs only — never invented.",
    }
