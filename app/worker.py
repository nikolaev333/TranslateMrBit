"""In-process durable job worker (one active transcription at a time)."""

from __future__ import annotations

import asyncio
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from app.database import utc_now
from app.models import JobStatus

if TYPE_CHECKING:
    from app.config import Settings
    from app.database import Database
    from app.whisper_engine import WhisperEngine

logger = logging.getLogger(__name__)


class TranscriptionWorker:
    def __init__(
        self,
        db: Database,
        engine: WhisperEngine,
        settings: Settings,
    ) -> None:
        self.db = db
        self.engine = engine
        self.settings = settings
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, settings.max_concurrent_jobs),
            thread_name_prefix="whisper-worker",
        )

    async def start(self) -> None:
        recovered = await self.db.requeue_interrupted_jobs()
        if recovered:
            logger.info("Requeued %s interrupted job(s)", recovered)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="transcription-worker")
        logger.info("Transcription worker started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
        logger.info("Transcription worker stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = await self.db.claim_next_job()
                if job is None:
                    try:
                        await asyncio.wait_for(
                            self._stop.wait(),
                            timeout=self.settings.worker_poll_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        pass
                    continue
                await self._process_job(job)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Worker loop error")
                await asyncio.sleep(2)

    async def _process_job(self, job: dict) -> None:
        job_id = job["id"]
        logger.info("Processing job %s (%s)", job_id, job["original_filename"])
        loop = asyncio.get_running_loop()

        def progress(message: str) -> None:
            # Fire-and-forget progress updates from the worker thread
            fut = asyncio.run_coroutine_threadsafe(
                self.db.update_job(job_id, progress_message=message),
                loop,
            )
            try:
                fut.result(timeout=5)
            except Exception:
                logger.debug("Progress update failed for %s", job_id)

        try:
            upload_path = job.get("upload_path") or ""
            if not upload_path or not Path(upload_path).is_file():
                raise FileNotFoundError("Uploaded audio file is missing")

            language = job.get("language") or self.settings.whisper_language
            model_name = job.get("model_name") or self.settings.whisper_model
            task = job.get("task") or "transcribe"

            result = await loop.run_in_executor(
                self._executor,
                lambda: self.engine.transcribe(
                    upload_path,
                    model_name=model_name,
                    language=language,
                    task=task,
                    progress_callback=progress,
                ),
            )

            output_dir = Path(self.settings.output_dir) / job_id
            paths = await loop.run_in_executor(
                self._executor,
                lambda: self.engine.write_outputs(result, output_dir, job_id),
            )

            text = (result.get("text") or "").strip()
            # Prefer the written TXT file content for fidelity with Whisper CLI
            txt_path = paths.get("txt")
            if txt_path and txt_path.is_file():
                text = txt_path.read_text(encoding="utf-8")

            await self.db.update_job(
                job_id,
                status=JobStatus.COMPLETED.value,
                progress_message="Completed",
                error_message=None,
                transcription_text=text,
                detected_language=result.get("language"),
                duration_seconds=_safe_float(result.get("duration")),
                output_txt_path=str(paths.get("txt") or ""),
                output_srt_path=str(paths.get("srt") or ""),
                output_vtt_path=str(paths.get("vtt") or ""),
                output_json_path=str(paths.get("json") or ""),
                completed_at=utc_now(),
            )
            logger.info("Job %s completed", job_id)
        except Exception as exc:
            err = _safe_error_message(exc)
            logger.error("Job %s failed: %s\n%s", job_id, err, traceback.format_exc())
            await self.db.update_job(
                job_id,
                status=JobStatus.FAILED.value,
                progress_message="Failed",
                error_message=err,
                completed_at=utc_now(),
            )


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_error_message(exc: BaseException) -> str:
    """User-facing error without leaking internals/secrets."""
    name = type(exc).__name__
    message = str(exc).strip() or "Unexpected error"
    # Truncate and strip potential path secrets beyond basename context
    if len(message) > 400:
        message = message[:400] + "…"
    lowered = message.lower()
    for secret_key in ("password", "session_secret", "secret", "token"):
        if secret_key in lowered:
            return f"{name}: Processing failed"
    if "cuda" in lowered and "out of memory" in lowered:
        return "GPU out of memory. Try a smaller model or free GPU memory."
    if "ffmpeg" in lowered or "ffprobe" in lowered:
        return "Audio decoding failed (FFmpeg error). Check that the file is a valid media file."
    return f"{name}: {message}"
