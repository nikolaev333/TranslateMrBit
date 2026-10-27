"""HTML page routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import is_authenticated, require_auth
from app.config import SUPPORTED_LANGUAGES, SUPPORTED_MODELS, Settings, get_settings
from app.database import Database
from app.device_info import configured_device_label
from app.model_disk import list_model_download_status
from app.services.smart_processing.resource_monitor import collect_resources

router = APIRouter(tags=["pages"])


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def get_db(request: Request) -> Database:
    return request.app.state.db


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value < 1024:
        return f"{value} B"
    if value < 1024**2:
        return f"{value / 1024:.1f} KB"
    if value < 1024**3:
        return f"{value / 1024**2:.1f} MB"
    return f"{value / 1024**3:.2f} GB"


def _overview_stats(by_status: dict[str, int]) -> dict[str, int]:
    processing_keys = {
        "queued",
        "waiting_for_resources",
        "preparing",
        "processing",
        "finalizing",
    }
    return {
        "total": sum(by_status.values()),
        "completed": int(by_status.get("completed") or 0),
        "failed": int(by_status.get("failed") or 0),
        "processing": sum(int(by_status.get(k) or 0) for k in processing_keys),
    }


@router.get("/", response_class=HTMLResponse)
async def overview_page(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Database, Depends(get_db)],
    _: Annotated[None, Depends(require_auth)],
):
    templates = get_templates(request)
    by_status = await db.count_jobs_by_status()
    resources = collect_resources(
        upload_dir=settings.upload_dir,
        output_dir=settings.output_dir,
        model_dir=settings.whisper_model_dir,
    )
    disk = resources.get("disk") or {}
    installed = [
        d
        for d in list_model_download_status(settings.whisper_model_dir, SUPPORTED_MODELS)
        if d.get("downloaded")
    ]
    return templates.TemplateResponse(
        "overview.html",
        {
            "request": request,
            "stats": _overview_stats(by_status),
            "storage": {
                "uploads_label": _fmt_bytes(disk.get("uploads_bytes")),
                "outputs_label": _fmt_bytes(disk.get("outputs_bytes")),
                "models_label": (
                    f"{len(installed)} installed · {_fmt_bytes(disk.get('models_bytes'))}"
                ),
            },
            "default_model": settings.whisper_model,
            "device_label": configured_device_label(settings),
        },
    )


@router.get("/transcribe", response_class=HTMLResponse)
async def transcribe_page(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(require_auth)],
):
    templates = get_templates(request)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "models": SUPPORTED_MODELS,
            "languages": SUPPORTED_LANGUAGES,
            "default_model": settings.whisper_model,
            "default_language": settings.whisper_language,
            "max_upload_mb": settings.max_upload_mb,
            "allowed_extensions": sorted(settings.allowed_extension_set),
        },
    )


@router.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    _: Annotated[None, Depends(require_auth)],
):
    templates = get_templates(request)
    return templates.TemplateResponse(
        "history.html",
        {"request": request},
    )


@router.get("/vocabulary", response_class=HTMLResponse)
async def vocabulary_page(
    request: Request,
    _: Annotated[None, Depends(require_auth)],
):
    templates = get_templates(request)
    return templates.TemplateResponse(
        "vocabulary.html",
        {"request": request},
    )


@router.get("/studio/{job_id}", response_class=HTMLResponse)
async def studio_page(
    job_id: str,
    request: Request,
    _: Annotated[None, Depends(require_auth)],
):
    templates = get_templates(request)
    return templates.TemplateResponse(
        "studio.html",
        {"request": request, "job_id": job_id},
    )


@router.get("/quality/{job_id}", response_class=HTMLResponse)
async def quality_page(
    job_id: str,
    request: Request,
    _: Annotated[None, Depends(require_auth)],
):
    templates = get_templates(request)
    return templates.TemplateResponse(
        "quality.html",
        {"request": request, "job_id": job_id},
    )


@router.get("/processing", response_class=HTMLResponse)
async def processing_page(
    request: Request,
    _: Annotated[None, Depends(require_auth)],
):
    templates = get_templates(request)
    return templates.TemplateResponse(
        "processing.html",
        {"request": request},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(require_auth)],
):
    templates = get_templates(request)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "default_model": settings.whisper_model,
            "default_language": settings.whisper_language,
            "device_label": configured_device_label(settings),
            "inference_backend": settings.inference_backend,
            "max_upload_mb": settings.max_upload_mb,
            "max_concurrent_jobs": settings.max_concurrent_jobs,
            "smart_processing": settings.smart_processing_enabled,
        },
    )


@router.get("/app", include_in_schema=False)
async def app_alias(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
):
    if not is_authenticated(request, settings):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
