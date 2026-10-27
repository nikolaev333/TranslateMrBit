"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.cleanup import UploadCleanupTask
from app.config import get_settings
from app.database import Database
from app.model_preload import ModelPreloader
from app.routers import auth_routes, health, jobs, models, pages
from app.whisper_engine import WhisperEngine
from app.worker import TranscriptionWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("whisper_app")

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_directories()

    if settings.is_production:
        if settings.app_password in {"", "CHANGE_ME"}:
            logger.warning(
                "APP_PASSWORD is still the default placeholder — change it before exposing publicly"
            )
        if settings.session_secret in {"", "CHANGE_ME_TO_LONG_RANDOM_SECRET"}:
            logger.warning(
                "SESSION_SECRET is still the default placeholder — change it before exposing publicly"
            )

    db = Database(settings.db_path)
    await db.connect()
    engine = WhisperEngine(settings)
    worker = TranscriptionWorker(db, engine, settings)
    cleanup = UploadCleanupTask(db, auto_delete_days=settings.auto_delete_uploads_days)
    model_preloader = ModelPreloader(engine)

    app.state.settings = settings
    app.state.db = db
    app.state.engine = engine
    app.state.worker = worker
    app.state.cleanup = cleanup
    app.state.model_preloader = model_preloader
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    await worker.start()
    await cleanup.start()
    logger.info(
        "Whisper AI app v%s ready (device=%s, default_model=%s)",
        __version__,
        engine.device,
        settings.whisper_model,
    )
    try:
        yield
    finally:
        await cleanup.stop()
        await worker.stop()
        await db.close()


app = FastAPI(
    title="Whisper AI Transcription",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# X-Forwarded-* from Coolify / Traefik / Nginx is handled by Uvicorn
# (--proxy-headers --forwarded-allow-ips "*") in the container CMD.

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(health.router)
app.include_router(auth_routes.router)
app.include_router(pages.router)
app.include_router(jobs.router)
app.include_router(models.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Preserve redirects used by HTML auth flow
    if exc.status_code in {301, 302, 303, 307, 308} and exc.headers:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(
            url=exc.headers.get("Location", "/login"),
            status_code=exc.status_code,
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Invalid request", "errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s: %s\n%s", request.url.path, exc, traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


def create_app() -> FastAPI:
    return app
