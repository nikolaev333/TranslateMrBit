"""Durable job worker (runs in worker process or optionally embedded in API for tests)."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from app.database import utc_now
from app.logging_setup import log_event
from app.models import JobStatus
from app.process_metrics import new_cpu_sampler, sample_process_cpu_percent

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
            log_event(logger, "interrupted_jobs_failed", count=recovered)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="transcription-worker")
        log_event(logger, "transcription_worker_started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
        log_event(logger, "transcription_worker_stopped")

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
        model_name = job.get("model_name") or self.settings.whisper_model
        log_event(
            logger,
            "job_process_start",
            job_id=job_id,
            model=model_name,
            filename=job.get("original_filename"),
        )
        loop = asyncio.get_running_loop()
        cpu_state = new_cpu_sampler()
        throttle = {"t": 0.0, "pct": -1.0}

        def progress(message: str, percent: float | None = None) -> None:
            now = time.monotonic()
            cpu = sample_process_cpu_percent(cpu_state)
            pct_value = None if percent is None else max(0.0, min(100.0, float(percent)))
            should = (
                pct_value is None
                or throttle["pct"] < 0
                or abs(pct_value - throttle["pct"]) >= 1.0
                or now - throttle["t"] >= 1.0
                or pct_value >= 99.0
            )
            if not should:
                return
            throttle["t"] = now
            if pct_value is not None:
                throttle["pct"] = pct_value
            fields: dict = {"progress_message": message}
            if pct_value is not None:
                fields["progress_percent"] = pct_value
            if cpu is not None:
                fields["cpu_percent"] = round(cpu, 1)
            # Fire-and-forget: never block the worker thread waiting on the
            # event loop (that loop is awaiting this same executor).
            fut = asyncio.run_coroutine_threadsafe(
                self.db.update_job(job_id, **fields),
                loop,
            )

            def _log_progress_error(done) -> None:
                try:
                    done.result()
                except Exception:
                    logger.debug("Progress update failed for %s", job_id, exc_info=True)

            fut.add_done_callback(_log_progress_error)

        try:
            upload_path = job.get("upload_path") or ""
            if not upload_path or not Path(upload_path).is_file():
                raise FileNotFoundError("Uploaded audio file is missing")

            language = job.get("language") or self.settings.whisper_language
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

            progress("Writing output files", 97.0)

            output_dir = Path(self.settings.output_dir) / job_id
            paths = await loop.run_in_executor(
                self._executor,
                lambda: self.engine.write_outputs(result, output_dir, job_id),
            )

            text = (result.get("text") or "").strip()
            txt_path = paths.get("txt")
            if txt_path and txt_path.is_file():
                text = txt_path.read_text(encoding="utf-8")

            await self.db.update_job(
                job_id,
                status=JobStatus.COMPLETED.value,
                progress_message="Completed",
                progress_percent=100.0,
                cpu_percent=None,
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
            log_event(logger, "job_process_done", job_id=job_id, model=model_name)
        except Exception as exc:
            err = _safe_error_message(exc)
            log_event(
                logger,
                "job_process_failed",
                job_id=job_id,
                model=model_name,
                error=err,
            )
            logger.error("Job %s failed: %s\n%s", job_id, err, traceback.format_exc())
            await self.db.update_job(
                job_id,
                status=JobStatus.FAILED.value,
                progress_message="Failed",
                cpu_percent=None,
                error_message=err,
                completed_at=utc_now(),
            )
        finally:
            if self.settings.unload_model_after_job:
                try:
                    self.engine.unload_model()
                    log_event(logger, "model_unloaded_after_job", job_id=job_id)
                except Exception:
                    logger.debug("unload_model failed", exc_info=True)


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
    if len(message) > 400:
        message = message[:400] + "…"
    lowered = message.lower()
    for secret_key in ("password", "session_secret", "secret", "token"):
        if secret_key in lowered:
            return f"{name}: Processing failed"
    if "cuda" in lowered and "out of memory" in lowered:
        return "GPU out of memory. Try a smaller model or free GPU memory."
    if "cannot allocate memory" in lowered or "out of memory" in lowered:
        return (
            "Out of memory while loading/running the model. "
            "Try model 'small' first, or add RAM. large needs ~8GB+ free."
        )
    if "killed" in lowered or "sigkill" in lowered:
        return (
            "Worker process was killed (likely OOM). "
            "Try a smaller model such as 'small' before 'large'."
        )
    if "ffmpeg" in lowered or "ffprobe" in lowered:
        return "Audio decoding failed (FFmpeg error). Check that the file is a valid media file."
    return f"{name}: {message}"
