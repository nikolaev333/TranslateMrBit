"""Whisper inference engine with model caching and device detection.

Importing this module is safe. Torch/whisper are imported lazily only when a
worker process actually downloads or loads a model.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from app.config import Settings, SUPPORTED_MODELS
from app.device_info import configured_device_label
from app.logging_setup import log_event
from app.model_disk import (
    CHECKPOINT_FILES,
    ensure_model_dir,
    is_model_downloaded as disk_is_model_downloaded,
    list_model_download_status as disk_list_status,
    model_checkpoint_path as disk_checkpoint_path,
)

logger = logging.getLogger(__name__)


class WhisperEngine:
    """Loads and reuses Whisper models; one engine per worker process."""

    CHECKPOINT_FILES = CHECKPOINT_FILES

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._model = None
        self._loaded_name: str | None = None
        self._device: str | None = None
        self._torch = None
        self._whisper = None

    def _import_deps(self) -> None:
        if self._torch is None:
            log_event(logger, "torch_import_start")
            import torch
            import whisper

            self._torch = torch
            self._whisper = whisper
            log_event(logger, "torch_import_done", torch_version=getattr(torch, "__version__", "?"))

    def detect_device(self) -> str:
        """Resolve runtime device. Imports torch — worker-only."""
        self._import_deps()
        configured = (self.settings.whisper_device or "auto").lower()
        if configured == "cpu":
            return "cpu"
        if configured == "cuda":
            if self._torch.cuda.is_available():
                return "cuda"
            logger.warning("WHISPER_DEVICE=cuda but CUDA unavailable; falling back to CPU")
            return "cpu"
        if self._torch.cuda.is_available():
            return "cuda"
        return "cpu"

    @property
    def device(self) -> str:
        if self._device is None:
            # Prefer configured label without torch until a real load happens.
            label = configured_device_label(self.settings)
            if label in {"cpu", "cuda"}:
                self._device = label
            else:
                self._device = self.detect_device()
        return self._device

    @property
    def loaded_model_name(self) -> str | None:
        return self._loaded_name

    def use_fp16(self) -> bool:
        if self.settings.whisper_fp16 is not None and self.settings.whisper_fp16 != "":
            return str(self.settings.whisper_fp16).lower() in {"1", "true", "yes", "on"}
        return self.device == "cuda"

    def download_root(self) -> Path:
        return ensure_model_dir(self.settings.whisper_model_dir)

    def model_checkpoint_path(self, model_name: str) -> Path | None:
        return disk_checkpoint_path(self.settings.whisper_model_dir, model_name)

    def is_model_downloaded(self, model_name: str) -> bool:
        return disk_is_model_downloaded(self.settings.whisper_model_dir, model_name)

    def download_model_weights(self, model_name: str) -> Path:
        """Download model weights to disk without loading them into RAM."""
        self._import_deps()
        models = getattr(self._whisper, "_MODELS", {}) or {}
        if model_name not in models:
            raise ValueError(f"Unknown Whisper model '{model_name}'")
        download_root = str(self.download_root())
        log_event(logger, "model_download_start", model=model_name, download_root=download_root)
        path = self._whisper._download(models[model_name], download_root, in_memory=False)
        log_event(logger, "model_download_done", model=model_name, path=path)
        return Path(path)

    def list_model_download_status(
        self, names: tuple[str, ...] | list[str] | None = None
    ) -> list[dict[str, Any]]:
        return disk_list_status(self.settings.whisper_model_dir, list(names or SUPPORTED_MODELS))

    def load_model(self, model_name: str | None = None) -> Any:
        name = model_name or self.settings.whisper_model
        if name not in SUPPORTED_MODELS and name not in {
            "tiny.en",
            "base.en",
            "small.en",
            "medium.en",
            "large-v1",
            "large-v2",
            "large-v3",
        }:
            logger.info("Loading non-default Whisper model name: %s", name)

        with self._lock:
            if self._model is not None and self._loaded_name == name:
                return self._model

            self._import_deps()
            device = self.detect_device()
            self._device = device
            download_root = str(self.download_root())

            log_event(
                logger,
                "model_load_start",
                model=name,
                device=device,
                download_root=download_root,
            )
            try:
                self._model = self._whisper.load_model(
                    name,
                    device=device,
                    download_root=download_root,
                )
            except Exception as exc:
                log_event(
                    logger,
                    "model_load_failed",
                    model=name,
                    device=device,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            self._loaded_name = name
            log_event(logger, "model_load_done", model=name, device=device)
            return self._model

    def unload_model(self) -> None:
        """Drop cached model references so RAM can be reclaimed between jobs."""
        with self._lock:
            self._model = None
            self._loaded_name = None
        try:
            if self._torch is not None and hasattr(self._torch, "cuda"):
                if self._torch.cuda.is_available():
                    self._torch.cuda.empty_cache()
        except Exception:
            logger.debug("cuda empty_cache skipped", exc_info=True)

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        model_name: str,
        language: str,
        task: str = "transcribe",
        progress_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Run transcription. Always uses task=transcribe unless explicitly overridden."""
        if progress_callback:
            progress_callback(f"Loading Whisper model '{model_name}'")

        model = self.load_model(model_name)

        lang = None if language.lower() in {"auto", "detect", ""} else language
        fp16 = self.use_fp16()

        if progress_callback:
            device = self.device
            progress_callback(
                f"Transcribing on {device.upper()}"
                + (" (FP16)" if fp16 else " (FP32)")
            )

        log_event(
            logger,
            "transcribe_start",
            model=model_name,
            language=language,
            task=task,
            device=self.device,
            audio=str(audio_path),
        )
        result = model.transcribe(
            str(audio_path),
            language=lang,
            task=task,
            fp16=fp16,
            verbose=False,
        )
        log_event(
            logger,
            "transcribe_done",
            model=model_name,
            language=result.get("language"),
            duration=result.get("duration"),
        )

        if progress_callback:
            progress_callback("Writing output files")

        return result

    def write_outputs(
        self,
        result: dict[str, Any],
        output_dir: Path,
        basename: str,
    ) -> dict[str, Path]:
        """Write txt/srt/vtt/json using Whisper writers."""
        self._import_deps()
        from whisper.utils import get_writer

        output_dir.mkdir(parents=True, exist_ok=True)
        audio_stub = output_dir / basename

        txt_writer = get_writer("txt", str(output_dir))
        srt_writer = get_writer("srt", str(output_dir))
        vtt_writer = get_writer("vtt", str(output_dir))
        json_writer = get_writer("json", str(output_dir))

        stub_path = str(audio_stub)
        txt_writer(result, stub_path)
        srt_writer(result, stub_path)
        vtt_writer(result, stub_path)
        json_writer(result, stub_path)

        paths = {
            "txt": output_dir / f"{basename}.txt",
            "srt": output_dir / f"{basename}.srt",
            "vtt": output_dir / f"{basename}.vtt",
            "json": output_dir / f"{basename}.json",
        }
        for path in paths.values():
            if not path.exists():
                logger.warning("Expected output missing: %s", path)
        return paths
