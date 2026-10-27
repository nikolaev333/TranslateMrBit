"""HTML page routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import is_authenticated, require_auth
from app.config import SUPPORTED_LANGUAGES, SUPPORTED_MODELS, Settings, get_settings

router = APIRouter(tags=["pages"])


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
async def dashboard(
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


@router.get("/app", include_in_schema=False)
async def app_alias(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
):
    if not is_authenticated(request, settings):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
