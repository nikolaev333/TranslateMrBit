"""Separate worker process entrypoint — owns torch / Whisper / model downloads.

If this process OOMs (exit 137), the API container stays healthy and Coolify
keeps routing traffic. Failed jobs are marked when the worker restarts.
"""

from __future__ import annotations

import asyncio
import signal

from app import __version__
from app.config import get_settings
from app.database import Database
from app.device_info import configured_device_label
from app.logging_setup import log_event, setup_logging
from app.model_preload import PreloadWorker, reset_stale_preload_status
from app.whisper_engine import WhisperEngine
from app.worker import TranscriptionWorker

logger = setup_logging("whisper_worker")


async def _run() -> None:
    settings = get_settings()
    settings.ensure_directories()
    reset_stale_preload_status(settings)

    log_event(
        logger,
        "worker_startup",
        version=__version__,
        device=configured_device_label(settings),
        default_model=settings.whisper_model,
        model_dir=settings.whisper_model_dir,
    )

    db = Database(settings.db_path)
    await db.connect()
    engine = WhisperEngine(settings)
    worker = TranscriptionWorker(db, engine, settings)
    preloader = PreloadWorker(settings, engine)

    await worker.start()
    preloader.start()
    log_event(logger, "worker_ready", version=__version__)

    stop = asyncio.Event()

    def _handle_signal(*_args) -> None:
        log_event(logger, "worker_signal_shutdown")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows / limited environments
            signal.signal(sig, lambda *_: _handle_signal())

    await stop.wait()
    preloader.stop()
    await worker.stop()
    await db.close()
    log_event(logger, "worker_shutdown")


def main() -> None:
    try:
        asyncio.run(_run())
    except MemoryError as exc:
        log_event(logger, "worker_memory_error", error=str(exc))
        raise SystemExit(137) from exc


if __name__ == "__main__":
    main()
