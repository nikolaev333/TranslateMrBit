"""Model preload coordination.

API process: enqueue disk downloads via shared request file (no torch).
Worker process: execute downloads and publish status (may import torch).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import SUPPORTED_MODELS, Settings
from app.logging_setup import log_event
from app.model_disk import list_model_download_status
from app.preload_control import (
    clear_preload_request,
    default_idle_status,
    read_preload_request,
    read_preload_status,
    write_preload_request,
    write_preload_status,
)

logger = logging.getLogger(__name__)


class ModelPreloader:
    """API-side facade: request downloads and report disk/status snapshot."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _paths(self) -> tuple[str, str]:
        return self.settings.data_dir, self.settings.db_path

    def snapshot(self) -> dict[str, Any]:
        data_dir, db_path = self._paths()
        status = read_preload_status(data_dir, db_path)
        return {
            "running": bool(status.get("running")),
            "current": status.get("current"),
            "requested": list(status.get("requested") or []),
            "completed": list(status.get("completed") or []),
            "failed": dict(status.get("failed") or {}),
            "updated_at": status.get("updated_at"),
            "models": list_model_download_status(
                self.settings.whisper_model_dir, SUPPORTED_MODELS
            ),
        }

    @property
    def status(self) -> dict[str, Any]:
        return self.snapshot()

    def start(self, model_names: list[str] | None = None) -> dict[str, Any]:
        names = [n.strip() for n in (model_names or list(SUPPORTED_MODELS)) if n and n.strip()]
        unknown = [n for n in names if n not in SUPPORTED_MODELS]
        if unknown:
            raise ValueError(f"Unsupported models: {', '.join(unknown)}")

        data_dir, db_path = self._paths()
        current = read_preload_status(data_dir, db_path)
        if current.get("running"):
            return self.snapshot()

        pending = read_preload_request(data_dir, db_path)
        if pending and pending.get("models"):
            return self.snapshot()

        write_preload_status(
            data_dir,
            {
                "running": False,
                "current": None,
                "requested": names,
                "completed": [],
                "failed": {},
                "queued": True,
            },
            db_path,
        )
        write_preload_request(data_dir, names, db_path)
        log_event(logger, "preload_request_enqueued", models=",".join(names))
        return self.snapshot()


class PreloadWorker:
    """Worker-side downloader that polls the shared request file."""

    def __init__(self, settings: Settings, engine: Any) -> None:
        self.settings = settings
        self.engine = engine
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = False

    def _paths(self) -> tuple[str, str]:
        return self.settings.data_dir, self.settings.db_path

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="whisper-preload-worker",
            daemon=True,
        )
        self._thread.start()
        log_event(logger, "preload_worker_started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log_event(logger, "preload_worker_stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data_dir, db_path = self._paths()
                req = read_preload_request(data_dir, db_path)
                if req and req.get("models") and not self._busy:
                    names = [str(n) for n in req["models"]]
                    clear_preload_request(data_dir, db_path)
                    self._busy = True
                    try:
                        self._run(names)
                    finally:
                        self._busy = False
            except Exception:
                logger.exception("Preload worker loop error")
            self._stop.wait(2.0)

    def _run(self, names: list[str]) -> None:
        data_dir, db_path = self._paths()
        status = {
            "running": True,
            "current": None,
            "requested": names,
            "completed": [],
            "failed": {},
        }
        write_preload_status(data_dir, status, db_path)
        log_event(logger, "preload_run_start", models=",".join(names))
        try:
            for name in names:
                status["current"] = name
                write_preload_status(data_dir, status, db_path)
                try:
                    if self.engine.is_model_downloaded(name):
                        log_event(logger, "model_download_skip", model=name, reason="already_on_disk")
                    else:
                        self.engine.download_model_weights(name)
                    status["completed"].append(name)
                except Exception as exc:  # noqa: BLE001 — isolate per-model failures
                    logger.exception("Failed to download model '%s'", name)
                    status["failed"][name] = str(exc)
                    log_event(
                        logger,
                        "model_download_failed",
                        model=name,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                write_preload_status(data_dir, status, db_path)
        finally:
            status["running"] = False
            status["current"] = None
            write_preload_status(data_dir, status, db_path)
            log_event(
                logger,
                "preload_run_done",
                completed=",".join(status["completed"]),
                failed=",".join(status["failed"].keys()),
            )


def reset_stale_preload_status(settings: Settings) -> None:
    """Clear 'running' flags left by a killed worker process."""
    status = read_preload_status(settings.data_dir, settings.db_path)
    if status.get("running"):
        status["running"] = False
        status["current"] = None
        status["error"] = "Preload interrupted by worker restart"
        write_preload_status(settings.data_dir, status, settings.db_path)
        log_event(logger, "preload_status_reset_after_restart")
    elif not status.get("requested") and not status.get("completed"):
        write_preload_status(settings.data_dir, default_idle_status(), settings.db_path)
