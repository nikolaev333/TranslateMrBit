"""Optional faster-whisper (CTranslate2) backend.

Requires optional dependency: pip install faster-whisper
Never imported at FastAPI startup. Loaded only when selected by a worker.
"""

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


class FasterWhisperBackend(TranscriptionBackend):
    backend_id = "faster-whisper"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._loaded_name: str | None = None
        self._WhisperModel = None

    def _import(self) -> None:
        if self._WhisperModel is not None:
            return
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Install optional dependency: pip install faster-whisper"
            ) from exc
        self._WhisperModel = WhisperModel

    def get_capabilities(self) -> BackendCapabilities:
        available = True
        try:
            self._import()
        except RuntimeError:
            available = False
        return BackendCapabilities(
            backend_id=self.backend_id,
            devices=["cpu", "cuda"],
            precisions=["fp32", "fp16", "int8"],
            supports_initial_prompt=True,
            supports_word_timestamps=True,
            optional_dependency=None if available else "faster-whisper",
        )

    def get_model_info(self) -> dict[str, Any]:
        return {
            "backend": self.backend_id,
            "loaded_model": self._loaded_name,
            "device": self.settings.whisper_device,
            "installed": self.get_capabilities().optional_dependency is None,
        }

    def load_model(self, model_name: str) -> Any:
        self._import()
        device = (self.settings.whisper_device or "cpu").lower()
        if device not in {"cpu", "cuda"}:
            device = "cpu"
        compute = "int8" if device == "cpu" else "float16"
        if self.settings.whisper_fp16 is not None and str(self.settings.whisper_fp16):
            if str(self.settings.whisper_fp16).lower() in {"1", "true", "yes", "on"}:
                compute = "float16"
            else:
                compute = "float32" if device == "cuda" else "int8"
        download_root = self.settings.whisper_model_dir
        self._model = self._WhisperModel(
            model_name,
            device=device,
            compute_type=compute,
            download_root=download_root,
        )
        self._loaded_name = model_name
        return self._model

    def unload_model(self) -> None:
        self._model = None
        self._loaded_name = None

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
        if self._model is None or self._loaded_name != model_name:
            if progress_callback:
                progress_callback(f"Loading faster-whisper '{model_name}'", 5.0)
            self.load_model(model_name)

        lang = None if language.lower() in {"auto", "detect", ""} else language
        if progress_callback:
            progress_callback("Transcribing (faster-whisper)", 10.0)

        assert self._model is not None
        segments_iter, info = self._model.transcribe(
            str(audio_path),
            language=lang,
            task=task,
            initial_prompt=initial_prompt,
            word_timestamps=word_timestamps,
        )
        segments: list[dict[str, Any]] = []
        texts: list[str] = []
        for i, seg in enumerate(segments_iter):
            text = (seg.text or "").strip()
            texts.append(text)
            item: dict[str, Any] = {
                "id": i,
                "start": float(seg.start),
                "end": float(seg.end),
                "text": text,
            }
            if word_timestamps and getattr(seg, "words", None):
                item["words"] = [
                    {
                        "start": float(w.start),
                        "end": float(w.end),
                        "word": w.word,
                    }
                    for w in seg.words
                    if w.start is not None and w.end is not None
                ]
            # Optional metadata when provided by faster-whisper
            if hasattr(seg, "avg_logprob"):
                item["avg_logprob"] = seg.avg_logprob
            if hasattr(seg, "compression_ratio"):
                item["compression_ratio"] = seg.compression_ratio
            if hasattr(seg, "no_speech_prob"):
                item["no_speech_prob"] = seg.no_speech_prob
            segments.append(item)
            if progress_callback and i % 5 == 0:
                progress_callback(f"Transcribing… segment {i + 1}", None)

        duration = getattr(info, "duration", None)
        detected = getattr(info, "language", lang)
        return TranscriptionResult(
            text=" ".join(t for t in texts if t).strip(),
            language=detected,
            duration=float(duration) if duration is not None else None,
            segments=segments,
            model_name=model_name,
            backend=self.backend_id,
            metadata={},
        )
