"""Health check endpoint (unauthenticated for Docker/Coolify probes)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    engine = getattr(request.app.state, "engine", None)
    device = "unknown"
    model_loaded = None
    if engine is not None:
        try:
            device = engine.device
            model_loaded = engine.loaded_model_name
        except Exception:
            device = "unknown"
    return HealthResponse(
        status="ok",
        version=__version__,
        device=device,
        model_loaded=model_loaded,
    )
