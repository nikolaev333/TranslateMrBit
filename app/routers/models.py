"""Model download status and preload endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.config import SUPPORTED_MODELS

router = APIRouter(
    prefix="/api/models",
    tags=["models"],
    dependencies=[Depends(require_auth)],
)


class PreloadRequest(BaseModel):
    models: list[str] | None = Field(
        default=None,
        description="Model names to download; defaults to all supported models",
    )


class PreloadResponse(BaseModel):
    message: str
    status: dict[str, Any]


@router.get("/status")
@router.get("/preload/status")
async def models_status(request: Request) -> dict[str, Any]:
    return request.app.state.model_preloader.snapshot()


@router.post("/preload", response_model=PreloadResponse)
async def preload_models(
    request: Request,
    body: PreloadRequest | None = None,
) -> PreloadResponse:
    preloader = request.app.state.model_preloader
    already_running = preloader.snapshot().get("running")
    names = (body.models if body else None) or list(SUPPORTED_MODELS)
    try:
        status_payload = preloader.start(names)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if already_running:
        message = "Model download already in progress"
    else:
        message = (
            "Model download queued for the worker process "
            "(weights only, not loaded into API RAM)"
        )

    return PreloadResponse(message=message, status=status_payload)
