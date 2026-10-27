"""Pytest fixtures with mocked Whisper inference."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

TEST_ROOT = Path(__file__).resolve().parent / "_tmp"


def _base_env() -> None:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("APP_PASSWORD", "test-password-123")
    os.environ.setdefault(
        "SESSION_SECRET", "test-session-secret-please-change-32chars"
    )
    os.environ.setdefault("SESSION_HTTPS_ONLY", "false")
    os.environ.setdefault("WHISPER_MODEL", "large")
    os.environ.setdefault("WHISPER_LANGUAGE", "Bulgarian")
    os.environ.setdefault("WHISPER_DEVICE", "cpu")
    os.environ.setdefault("MAX_UPLOAD_MB", "10")
    os.environ.setdefault("MAX_CONCURRENT_JOBS", "1")
    os.environ.setdefault("AUTO_DELETE_UPLOADS_DAYS", "0")
    os.environ.setdefault("WORKER_POLL_INTERVAL_SECONDS", "0.2")
    # Tests run the worker inside the API process with a fake engine.
    os.environ.setdefault("RUN_EMBEDDED_WORKER", "true")


_base_env()

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


class FakeWhisperEngine:
    """Drop-in replacement that never downloads models."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._device = "cpu"
        self._loaded_name: str | None = None
        self.fail_next = False
        self.last_call: dict[str, Any] | None = None
        self.unload_calls = 0

    @property
    def device(self) -> str:
        return self._device

    @property
    def loaded_model_name(self) -> str | None:
        return self._loaded_name

    def load_model(self, model_name: str | None = None) -> Any:
        self._loaded_name = model_name or "large"
        return object()

    def unload_model(self) -> None:
        self.unload_calls += 1
        self._loaded_name = None

    def is_model_downloaded(self, model_name: str) -> bool:
        return False

    def download_model_weights(self, model_name: str) -> Path:
        return Path(f"/models/{model_name}.pt")

    def list_model_download_status(self, names=None):
        return []

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        model_name: str,
        language: str,
        task: str = "transcribe",
        progress_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        self.last_call = {
            "audio_path": str(audio_path),
            "model_name": model_name,
            "language": language,
            "task": task,
        }
        if progress_callback:
            progress_callback("Fake transcription in progress")
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("Simulated Whisper failure")
        text = "Това е тестова транскрипция на български."
        return {
            "text": text,
            "language": "bg" if language.lower().startswith("bulgar") else language,
            "duration": 1.23,
            "segments": [
                {
                    "id": 0,
                    "seek": 0,
                    "start": 0.0,
                    "end": 1.23,
                    "text": text,
                    "tokens": [],
                    "temperature": 0.0,
                    "avg_logprob": -0.1,
                    "compression_ratio": 1.0,
                    "no_speech_prob": 0.01,
                }
            ],
        }

    def write_outputs(
        self, result: dict[str, Any], output_dir: Path, basename: str
    ) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "txt": output_dir / f"{basename}.txt",
            "srt": output_dir / f"{basename}.srt",
            "vtt": output_dir / f"{basename}.vtt",
            "json": output_dir / f"{basename}.json",
        }
        text = (result.get("text") or "").strip() + "\n"
        paths["txt"].write_text(text, encoding="utf-8")
        paths["srt"].write_text(
            "1\n00:00:00,000 --> 00:00:01,230\n" + text,
            encoding="utf-8",
        )
        paths["vtt"].write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.230\n" + text,
            encoding="utf-8",
        )
        import json

        paths["json"].write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
        if self.last_call:
            self._loaded_name = self.last_call["model_name"]
        return paths


async def fake_probe_media(path: Path) -> dict:
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        raise HTTPException(
            status_code=400, detail="Invalid or unsupported media file"
        )
    data = p.read_bytes()[:16]
    if data.startswith(b"NOTMEDIA") or data.startswith(b"%PDF"):
        raise HTTPException(
            status_code=400, detail="Invalid or unsupported media file"
        )
    return {
        "format": {"format_name": "mp3", "duration": "1.0"},
        "streams": [{"codec_type": "audio", "codec_name": "mp3"}],
    }


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    from app.auth import rate_limiter

    get_settings.cache_clear()
    rate_limiter._attempts.clear()
    yield
    get_settings.cache_clear()
    rate_limiter._attempts.clear()


@pytest_asyncio.fixture
async def client(monkeypatch, tmp_path):
    upload = tmp_path / "uploads"
    output = tmp_path / "outputs"
    dbdir = tmp_path / "db"
    models = tmp_path / "models"
    for p in (upload, output, dbdir, models):
        p.mkdir()

    os.environ.update(
        {
            "UPLOAD_DIR": str(upload),
            "OUTPUT_DIR": str(output),
            "DB_PATH": str(dbdir / "test.db"),
            "WHISPER_MODEL_DIR": str(models),
            "DATA_DIR": str(tmp_path),
            "SESSION_HTTPS_ONLY": "false",
            "APP_PASSWORD": "test-password-123",
            "SESSION_SECRET": "test-session-secret-please-change-32chars",
            "APP_ENV": "test",
            "AUTO_DELETE_UPLOADS_DAYS": "0",
            "WORKER_POLL_INTERVAL_SECONDS": "0.2",
            "RUN_EMBEDDED_WORKER": "true",
            "WHISPER_DEVICE": "cpu",
        }
    )
    get_settings.cache_clear()

    fake = FakeWhisperEngine()
    monkeypatch.setattr("app.whisper_engine.WhisperEngine", lambda settings: fake)
    monkeypatch.setattr("app.media.probe_media", fake_probe_media)

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        app.state.fake_engine = fake
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def login(client: AsyncClient, password: str = "test-password-123") -> None:
    res = await client.post("/api/login", json={"password": password})
    assert res.status_code == 200, res.text
