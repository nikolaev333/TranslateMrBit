"""Background download-only Whisper model preloader (no RAM load)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import SUPPORTED_MODELS
from app.whisper_engine import WhisperEngine

logger = logging.getLogger(__name__)


class ModelPreloader:
    def __init__(self, engine: WhisperEngine) -> None:
        self.engine = engine
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._current: str | None = None
        self._completed: list[str] = []
        self._failed: dict[str, str] = {}
        self._requested: list[str] = []

    def snapshot(self) -> dict[str, Any]:
        """Lightweight status — disk checks only, no torch import."""
        with self._lock:
            running = self._running
            current = self._current
            requested = list(self._requested)
            completed = list(self._completed)
            failed = dict(self._failed)
        return {
            "running": running,
            "current": current,
            "requested": requested,
            "completed": completed,
            "failed": failed,
            "models": self.engine.list_model_download_status(SUPPORTED_MODELS),
        }

    @property
    def status(self) -> dict[str, Any]:
        return self.snapshot()

    def start(self, model_names: list[str] | None = None) -> dict[str, Any]:
        names = [n.strip() for n in (model_names or list(SUPPORTED_MODELS)) if n and n.strip()]
        unknown = [n for n in names if n not in SUPPORTED_MODELS]
        if unknown:
            raise ValueError(f"Unsupported models: {', '.join(unknown)}")

        with self._lock:
            if self._running:
                # Do not call snapshot while holding the lock.
                already = True
            else:
                already = False
                self._running = True
                self._current = None
                self._completed = []
                self._failed = {}
                self._requested = names
                self._thread = threading.Thread(
                    target=self._run,
                    args=(names,),
                    name="whisper-model-preload",
                    daemon=True,
                )
                self._thread.start()

        return self.snapshot()

    def _run(self, names: list[str]) -> None:
        try:
            for name in names:
                with self._lock:
                    self._current = name
                try:
                    if self.engine.is_model_downloaded(name):
                        logger.info("Model '%s' already on disk — skipping download", name)
                    else:
                        self.engine.download_model_weights(name)
                    with self._lock:
                        self._completed.append(name)
                except Exception as exc:  # noqa: BLE001 — isolate per-model failures
                    logger.exception("Failed to download model '%s'", name)
                    with self._lock:
                        self._failed[name] = str(exc)
        finally:
            with self._lock:
                self._running = False
                self._current = None
