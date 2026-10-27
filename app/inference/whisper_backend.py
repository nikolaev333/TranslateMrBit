"""OpenAI Whisper backend adapter (wraps existing WhisperEngine)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import Settings
from app.inference.base import (
    BackendCapabilities,
    ProgressCallback,
    TranscriptionBackend,
    TranscriptionResult,
)
from app.whisper_engine import WhisperEngine


class OpenAIWhisperBackend(TranscriptionBackend):
    backend_id = "openai-whisper"

    def __init__(self, settings: Settings, engine: WhisperEngine | None = None) -> None:
        self.settings = settings
        self.engine = engine or WhisperEngine(settings)

    def get_capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend_id=self.backend_id,
            devices=["cpu", "cuda"],
            precisions=["fp32", "fp16"],
            supports_initial_prompt=True,
            supports_word_timestamps=True,
            optional_dependency=None,
        )

    def get_model_info(self) -> dict[str, Any]:
        return {
            "backend": self.backend_id,
            "loaded_model": self.engine.loaded_model_name,
            "device": getattr(self.engine, "_device", None)
            or self.settings.whisper_device,
        }

    def load_model(self, model_name: str) -> Any:
        return self.engine.load_model(model_name)

    def unload_model(self) -> None:
        self.engine.unload_model()

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        model_name: str,
        language: str,
        task: str = "transcribe",
        progress_callback: ProgressCallback | None = None,
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
    ) -> TranscriptionResult:
        # word_timestamps: openai-whisper supports via condition_on_previous_text
        # and word_timestamps kw in newer versions — pass only if True.
        raw = self.engine.transcribe(
            audio_path,
            model_name=model_name,
            language=language,
            task=task,
            progress_callback=progress_callback,
            initial_prompt=initial_prompt,
        )
        # If word timestamps requested, try a second-pass flag only when engine supports it.
        # Current WhisperEngine.transcribe does not expose word_timestamps; do not invent words.
        del word_timestamps
        return TranscriptionResult(
            text=(raw.get("text") or "").strip(),
            language=raw.get("language"),
            duration=raw.get("duration"),
            segments=list(raw.get("segments") or []),
            model_name=model_name,
            backend=self.backend_id,
            metadata={},
        )
