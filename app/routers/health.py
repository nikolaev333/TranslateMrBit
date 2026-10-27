"""Health check endpoint (unauthenticated for Docker/Coolify probes).

Must never import or call into torch / Whisper — probes must stay fast even
while a separate worker process loads models or OOMs.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__
from app.config import get_settings
from app.device_info import configured_device_label
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    device = configured_device_label(settings)
    # model_loaded is informational only — null does not mean unhealthy.
    model_loaded = None
    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        model_loaded = getattr(engine, "loaded_model_name", None)
    return HealthResponse(
        status="ok",
        version=__version__,
        device=device,
        model_loaded=model_loaded,
    )
