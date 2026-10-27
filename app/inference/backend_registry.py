"""Backend factory / registry."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.inference.base import TranscriptionBackend
from app.inference.faster_whisper_backend import FasterWhisperBackend
from app.inference.whisper_backend import OpenAIWhisperBackend


def list_backends(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or Settings()
    backends: list[TranscriptionBackend] = [
        OpenAIWhisperBackend(settings),
        FasterWhisperBackend(settings),
    ]
    out = []
    for b in backends:
        caps = b.get_capabilities()
        info = caps.to_dict()
        info["available"] = caps.optional_dependency is None
        out.append(info)
    return out


def get_backend(
    backend_id: str,
    settings: Settings,
    *,
    whisper_engine: Any | None = None,
) -> TranscriptionBackend:
    name = (backend_id or "openai-whisper").strip().lower()
    if name in {"openai-whisper", "whisper", "openai"}:
        return OpenAIWhisperBackend(settings, engine=whisper_engine)
    if name in {"faster-whisper", "faster_whisper", "ctranslate2"}:
        return FasterWhisperBackend(settings)
    raise ValueError(f"Unknown inference backend '{backend_id}'")
