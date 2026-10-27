"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "production", "test"] = "production"
    app_port: int = 8000
    app_host: str = "0.0.0.0"
    app_password: str = Field(default="CHANGE_ME")
    session_secret: str = Field(default="CHANGE_ME_TO_LONG_RANDOM_SECRET")
    session_https_only: bool = True
    session_cookie_name: str = "whisper_session"
    session_max_age_hours: int = 168

    whisper_model: str = "large"
    whisper_language: str = "Bulgarian"
    # Prefer explicit cpu/cuda in production so /health never needs torch.
    whisper_device: str = "cpu"
    whisper_model_dir: str = "/models"
    whisper_fp16: str | None = None

    data_dir: str = "/data"
    upload_dir: str = "/data/uploads"
    output_dir: str = "/data/outputs"
    db_path: str = "/data/db/whisper.db"

    max_upload_mb: int = 500
    max_concurrent_jobs: int = 1
    auto_delete_uploads_days: int = 7
    allowed_extensions: str = (
        "mp3,wav,m4a,mp4,flac,ogg,webm,aac,wma,mkv,mov,opus"
    )

    login_rate_limit: int = 5
    login_rate_window_seconds: int = 60
    worker_poll_interval_seconds: float = 1.0
    # Production Docker runs a separate worker container. Tests enable this.
    run_embedded_worker: bool = False
    unload_model_after_job: bool = True

    # Inference backend: openai-whisper today; faster-whisper reserved for future.
    inference_backend: str = "openai-whisper"
    # MrBit Smart Vocabulary — Whisper initial_prompt length budget (chars).
    vocabulary_prompt_max_chars: int = 800
    # Auto-run Smart Vocabulary analysis after each completed job.
    vocabulary_auto_analyze: bool = True
    # Seed a few demo iGaming terms when the vocabulary table is empty.
    vocabulary_seed_defaults: bool = True

    # MrBit Quality Analyzer
    quality_auto_analyze: bool = False
    quality_include_audio: bool = True
    quality_max_audio_analyze_seconds: float = 600.0

    # MrBit Smart Processing Engine
    smart_processing_enabled: bool = True
    processing_safety_margin_gb: float = 0.75
    processing_enforce_memory_gate: bool = True
    processing_allow_unsafe_models: bool = False
    processing_enable_chunking: bool = False
    processing_chunk_seconds: float = 600.0
    processing_chunk_overlap_seconds: float = 1.0
    processing_job_timeout_seconds: float = 0.0  # 0 = disabled
    processing_max_retries: int = 2
    worker_id: str = ""

    @field_validator("app_password", "session_secret")
    @classmethod
    def warn_default_secrets(cls, value: str) -> str:
        return value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def allowed_extension_set(self) -> set[str]:
        return {
            ext.strip().lower().lstrip(".")
            for ext in self.allowed_extensions.split(",")
            if ext.strip()
        }

    @property
    def session_max_age_seconds(self) -> int:
        return self.session_max_age_hours * 3600

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def ensure_directories(self) -> None:
        for path in (
            self.upload_dir,
            self.output_dir,
            Path(self.db_path).parent,
            self.whisper_model_dir,
        ):
            Path(path).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Selectable Whisper model names (official openai-whisper package)
SUPPORTED_MODELS = ("tiny", "base", "small", "medium", "large", "turbo")

# Common language choices for the UI; Whisper accepts any language name/code
SUPPORTED_LANGUAGES = (
    "Bulgarian",
    "English",
    "German",
    "French",
    "Spanish",
    "Italian",
    "Portuguese",
    "Russian",
    "Ukrainian",
    "Turkish",
    "Polish",
    "Romanian",
    "Greek",
    "Dutch",
    "Swedish",
    "Czech",
    "Hungarian",
    "Japanese",
    "Chinese",
    "Korean",
    "Arabic",
    "auto",
)
