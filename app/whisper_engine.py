"""Whisper inference engine with model caching and device detection."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from app.config import Settings, SUPPORTED_MODELS

logger = logging.getLogger(__name__)


class WhisperEngine:
    """Loads and reuses Whisper models; one engine per process."""

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
            import torch
            import whisper

            self._torch = torch
            self._whisper = whisper

    def detect_device(self) -> str:
        self._import_deps()
        configured = (self.settings.whisper_device or "auto").lower()
        if configured == "cpu":
            return "cpu"
        if configured == "cuda":
            if self._torch.cuda.is_available():
                return "cuda"
            logger.warning("WHISPER_DEVICE=cuda but CUDA unavailable; falling back to CPU")
            return "cpu"
        # auto
        if self._torch.cuda.is_available():
            return "cuda"
        return "cpu"

    @property
    def device(self) -> str:
        if self._device is None:
            self._device = self.detect_device()
        return self._device

    @property
    def loaded_model_name(self) -> str | None:
        return self._loaded_name

    def use_fp16(self) -> bool:
        if self.settings.whisper_fp16 is not None and self.settings.whisper_fp16 != "":
            return str(self.settings.whisper_fp16).lower() in {"1", "true", "yes", "on"}
        return self.device == "cuda"

    # Official openai-whisper checkpoint filenames (avoid importing torch for status checks).
    CHECKPOINT_FILES = {
        "tiny": "tiny.pt",
        "base": "base.pt",
        "small": "small.pt",
        "medium": "medium.pt",
        "large": "large-v3.pt",
        "turbo": "large-v3-turbo.pt",
        "tiny.en": "tiny.en.pt",
        "base.en": "base.en.pt",
        "small.en": "small.en.pt",
        "medium.en": "medium.en.pt",
        "large-v1": "large-v1.pt",
        "large-v2": "large-v2.pt",
        "large-v3": "large-v3.pt",
    }

    def download_root(self) -> Path:
        root = Path(self.settings.whisper_model_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def model_checkpoint_path(self, model_name: str) -> Path | None:
        """Return expected on-disk checkpoint path without importing torch."""
        filename = self.CHECKPOINT_FILES.get(model_name)
        if not filename:
            return None
        return self.download_root() / filename

    def is_model_downloaded(self, model_name: str) -> bool:
        path = self.model_checkpoint_path(model_name)
        return bool(path and path.is_file() and path.stat().st_size > 0)

    def download_model_weights(self, model_name: str) -> Path:
        """Download model weights to disk without loading them into RAM."""
        self._import_deps()
        models = getattr(self._whisper, "_MODELS", {}) or {}
        if model_name not in models:
            raise ValueError(f"Unknown Whisper model '{model_name}'")
        download_root = str(self.download_root())
        logger.info("Downloading Whisper weights '%s' -> %s", model_name, download_root)
        path = self._whisper._download(models[model_name], download_root, in_memory=False)
        logger.info("Downloaded Whisper weights '%s' -> %s", model_name, path)
        return Path(path)

    def list_model_download_status(self, names: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
        names = list(names or SUPPORTED_MODELS)
        out: list[dict[str, Any]] = []
        for name in names:
            path = self.model_checkpoint_path(name)
            size = path.stat().st_size if path and path.is_file() else 0
            out.append(
                {
                    "name": name,
                    "downloaded": bool(path and path.is_file() and size > 0),
                    "path": str(path) if path else None,
                    "size_bytes": size,
                }
            )
        return out

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
            # Still allow the requested name if Whisper knows it; do not substitute.
            logger.info("Loading non-default Whisper model name: %s", name)

        with self._lock:
            if self._model is not None and self._loaded_name == name:
                return self._model

            self._import_deps()
            device = self.detect_device()
            self._device = device
            download_root = str(self.download_root())

            logger.info(
                "Loading Whisper model '%s' on device=%s (download_root=%s)",
                name,
                device,
                download_root,
            )
            # Intentionally load the exact requested model name — never substitute.
            self._model = self._whisper.load_model(
                name,
                device=device,
                download_root=download_root,
            )
            self._loaded_name = name
            logger.info("Whisper model '%s' ready", name)
            return self._model

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

        # Mirror local CLI: --task transcribe --language Bulgarian --model large
        result = model.transcribe(
            str(audio_path),
            language=lang,
            task=task,
            fp16=fp16,
            verbose=False,
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
        # Whisper writers expect an audio path basename; we pass a dummy stem via options
        audio_stub = output_dir / basename

        # Write using Whisper's official writers for fidelity with CLI output
        txt_writer = get_writer("txt", str(output_dir))
        srt_writer = get_writer("srt", str(output_dir))
        vtt_writer = get_writer("vtt", str(output_dir))
        json_writer = get_writer("json", str(output_dir))

        # Writers append their own extensions based on the audio path stem
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
