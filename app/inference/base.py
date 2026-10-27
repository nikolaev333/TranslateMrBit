"""Common transcription backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


ProgressCallback = Callable[..., None]


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    duration: float | None
    segments: list[dict[str, Any]] = field(default_factory=list)
    model_name: str = ""
    backend: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_whisper_compat(self) -> dict[str, Any]:
        """Shape compatible with existing worker / segment persistence."""
        return {
            "text": self.text,
            "language": self.language,
            "duration": self.duration,
            "segments": self.segments,
            "backend": self.backend,
            "model_name": self.model_name,
            **self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackendCapabilities:
    backend_id: str
    devices: list[str]
    precisions: list[str]
    supports_initial_prompt: bool
    supports_word_timestamps: bool
    optional_dependency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TranscriptionBackend(ABC):
    backend_id: str = "base"

    @abstractmethod
    def get_capabilities(self) -> BackendCapabilities: ...

    @abstractmethod
    def get_model_info(self) -> dict[str, Any]: ...

    @abstractmethod
    def load_model(self, model_name: str) -> Any: ...

    @abstractmethod
    def unload_model(self) -> None: ...

    @abstractmethod
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
    ) -> TranscriptionResult: ...
