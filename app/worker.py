"""Durable job worker (runs in worker process or optionally embedded in API for tests)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from app.database import utc_now
from app.inference.backend_registry import get_backend
from app.logging_setup import log_event
from app.models import JobStatus, SegmentsStatus
from app.process_metrics import new_cpu_sampler, sample_process_cpu_percent
from app.services.smart_processing.resource_monitor import collect_resources
from app.services.smart_processing.scheduler import can_admit_job
from app.services.smart_vocabulary import (
    analysis_to_dict,
    analyze_transcription,
    build_initial_prompt,
    term_from_row,
)
from app.services.transcript_segments import normalize_whisper_segments

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
        self.worker_id = (
            (settings.worker_id or "").strip()
            or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )

    async def start(self) -> None:
        recovered = await self.db.requeue_interrupted_jobs()
        if recovered:
            log_event(logger, "interrupted_jobs_failed", count=recovered)
        try:
            stale = await self.db.recover_stale_leases()
            if stale:
                log_event(logger, "stale_leases_requeued", count=stale)
        except Exception:
            logger.debug("stale lease recovery skipped", exc_info=True)
        try:
            timeout_sec = float(
                getattr(self.settings, "processing_job_timeout_seconds", 0) or 0
            )
            timed_out = await self.db.fail_timed_out_jobs(timeout_sec)
            if timed_out:
                log_event(logger, "jobs_timed_out", count=timed_out)
        except Exception:
            logger.debug("timeout sweep skipped", exc_info=True)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="transcription-worker")
        log_event(logger, "transcription_worker_started", worker_id=self.worker_id)

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
                try:
                    await self.db.upsert_worker_heartbeat(
                        self.worker_id, status="idle", hostname=socket.gethostname()
                    )
                except Exception:
                    logger.debug("heartbeat failed", exc_info=True)
                try:
                    timeout_sec = float(
                        getattr(self.settings, "processing_job_timeout_seconds", 0) or 0
                    )
                    if timeout_sec > 0:
                        await self.db.fail_timed_out_jobs(timeout_sec)
                    await self.db.recover_stale_leases()
                except Exception:
                    logger.debug("lease/timeout sweep skipped", exc_info=True)

                job = await self.db.claim_next_job(
                    worker_id=self.worker_id,
                    max_concurrent=self.settings.max_concurrent_jobs,
                )
                if job is None:
                    try:
                        await asyncio.wait_for(
                            self._stop.wait(),
                            timeout=self.settings.worker_poll_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        pass
                    continue

                # Resource-aware admission after atomic claim.
                if getattr(self.settings, "smart_processing_enabled", True):
                    resources = collect_resources(
                        upload_dir=self.settings.upload_dir,
                        output_dir=self.settings.output_dir,
                        model_dir=self.settings.whisper_model_dir,
                    )
                    decision = can_admit_job(
                        job,
                        running_count=0,
                        max_concurrent=self.settings.max_concurrent_jobs,
                        resources=resources,
                        safety_margin_gb=self.settings.processing_safety_margin_gb,
                        enforce_memory_gate=self.settings.processing_enforce_memory_gate
                        and not bool(job.get("allow_unsafe_model")),
                    )
                    if not decision.admit and decision.code == "waiting_resources":
                        await self.db.update_job(
                            job["id"],
                            status=JobStatus.WAITING_FOR_RESOURCES.value,
                            progress_message=decision.reason,
                            worker_id=None,
                            processing_stage="waiting_for_resources",
                        )
                        await asyncio.sleep(2)
                        continue
                    if not decision.admit and decision.code == "cancelled":
                        await self.db.update_job(
                            job["id"],
                            status=JobStatus.CANCELLED.value,
                            progress_message="Cancelled",
                            completed_at=utc_now(),
                        )
                        continue

                await self.db.upsert_worker_heartbeat(
                    self.worker_id,
                    status="busy",
                    current_job_id=job["id"],
                    hostname=socket.gethostname(),
                )
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

        t0 = time.monotonic()
        created = job.get("created_at")
        queue_wait = None
        try:
            if created:
                # ISO timestamps — best-effort wait metric
                from datetime import datetime

                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                queue_wait = max(0.0, (datetime.now(created_dt.tzinfo) - created_dt).total_seconds())
        except Exception:
            queue_wait = None
        model_load_sec = None
        inference_sec = None
        finalize_sec = None
        backend_name = (
            job.get("inference_backend")
            or getattr(self.settings, "inference_backend", None)
            or "openai-whisper"
        )

        try:
            # Honour cancellation before heavy work
            fresh = await self.db.get_job(job_id)
            if fresh and fresh.get("cancel_requested"):
                await self.db.update_job(
                    job_id,
                    status=JobStatus.CANCELLED.value,
                    progress_message="Cancelled",
                    completed_at=utc_now(),
                )
                return

            upload_path = job.get("upload_path") or ""
            if not upload_path or not Path(upload_path).is_file():
                raise FileNotFoundError("Uploaded audio file is missing")

            language = job.get("language") or self.settings.whisper_language
            task = job.get("task") or "transcribe"

            await self.db.update_job(
                job_id,
                status=JobStatus.PROCESSING.value,
                processing_stage="inference",
                progress_message="Transcribing",
                queue_wait_seconds=queue_wait,
            )

            # Smart Vocabulary: build short terminology context for Whisper.
            vocab_rows = await self.db.list_vocabulary_entries(
                enabled=True, limit=500, offset=0
            )
            terms = [term_from_row(r) for r in vocab_rows]
            initial_prompt = build_initial_prompt(
                terms,
                language=language,
                max_chars=self.settings.vocabulary_prompt_max_chars,
            )

            backend = get_backend(backend_name, self.settings, whisper_engine=self.engine)
            try:
                await self.db.renew_job_lease(
                    job_id,
                    lease_seconds=float(
                        getattr(self.settings, "processing_lease_seconds", 1800) or 1800
                    ),
                )
            except Exception:
                logger.debug("lease renew skipped", exc_info=True)

            t_inf0 = time.monotonic()
            tr = await loop.run_in_executor(
                self._executor,
                lambda: self._transcribe_with_optional_chunking(
                    backend,
                    upload_path=upload_path,
                    model_name=model_name,
                    language=language,
                    task=task,
                    progress_callback=progress,
                    initial_prompt=initial_prompt,
                    job_id=job_id,
                ),
            )
            inference_sec = time.monotonic() - t_inf0
            result = tr.to_whisper_compat() if hasattr(tr, "to_whisper_compat") else tr

            fresh = await self.db.get_job(job_id)
            if fresh and fresh.get("cancel_requested"):
                await self.db.update_job(
                    job_id,
                    status=JobStatus.CANCELLED.value,
                    progress_message="Cancelled after inference",
                    completed_at=utc_now(),
                )
                return

            progress("Writing output files", 97.0)
            await self.db.update_job(
                job_id,
                status=JobStatus.FINALIZING.value,
                processing_stage="finalizing",
            )
            t_fin0 = time.monotonic()

            output_dir = Path(self.settings.output_dir) / job_id
            paths = await loop.run_in_executor(
                self._executor,
                lambda: self.engine.write_outputs(result, output_dir, job_id),
            )

            text = (result.get("text") or "").strip()
            txt_path = paths.get("txt")
            if txt_path and txt_path.is_file():
                text = txt_path.read_text(encoding="utf-8")
            finalize_sec = time.monotonic() - t_fin0

            analysis_json = None
            if self.settings.vocabulary_auto_analyze and text:
                analysis = analyze_transcription(text, terms, language=language)
                analysis_json = json.dumps(analysis_to_dict(analysis), ensure_ascii=False)

            # Persist real Whisper timestamps for Transcription Studio.
            segments, has_words = normalize_whisper_segments(result)
            if segments:
                await self.db.replace_job_segments(
                    job_id,
                    segments,
                    has_word_timestamps=has_words,
                    status=SegmentsStatus.READY.value,
                )
                segments_status = SegmentsStatus.READY.value
            else:
                await self.db.update_job(
                    job_id,
                    segments_status=SegmentsStatus.UNSUPPORTED.value,
                    has_word_timestamps=0,
                )
                segments_status = SegmentsStatus.UNSUPPORTED.value

            duration = _safe_float(result.get("duration"))
            total_sec = time.monotonic() - t0
            rtf = None
            if duration and duration > 0:
                rtf = total_sec / duration
            peak_mem = None
            try:
                import psutil  # type: ignore

                peak_mem = int(psutil.Process(os.getpid()).memory_info().rss)
            except Exception:
                peak_mem = None

            await self.db.update_job(
                job_id,
                status=JobStatus.COMPLETED.value,
                progress_message="Completed",
                progress_percent=100.0,
                cpu_percent=None,
                error_message=None,
                transcription_text=text,
                detected_language=result.get("language"),
                duration_seconds=duration,
                output_txt_path=str(paths.get("txt") or ""),
                output_srt_path=str(paths.get("srt") or ""),
                output_vtt_path=str(paths.get("vtt") or ""),
                output_json_path=str(paths.get("json") or ""),
                vocabulary_prompt_used=initial_prompt,
                vocabulary_analysis_json=analysis_json,
                segments_status=segments_status,
                inference_backend=backend_name,
                model_load_seconds=model_load_sec,
                inference_seconds=inference_sec,
                finalize_seconds=finalize_sec,
                queue_wait_seconds=queue_wait,
                peak_memory_bytes=peak_mem,
                realtime_factor=rtf,
                processing_stage="completed",
                completed_at=utc_now(),
            )
            try:
                await self.db.save_processing_metrics(
                    {
                        "job_id": job_id,
                        "model_name": model_name,
                        "backend": backend_name,
                        "audio_duration_sec": duration,
                        "queue_wait_sec": queue_wait,
                        "model_load_sec": model_load_sec,
                        "inference_sec": inference_sec,
                        "finalize_sec": finalize_sec,
                        "total_sec": total_sec,
                        "peak_memory_bytes": peak_mem,
                        "realtime_factor": rtf,
                    }
                )
            except Exception:
                logger.debug("metrics save skipped", exc_info=True)
            if getattr(self.settings, "quality_auto_analyze", False):
                try:
                    await self.db.create_quality_run(
                        job_id,
                        include_audio=bool(
                            getattr(self.settings, "quality_include_audio", True)
                        ),
                    )
                    log_event(logger, "quality_run_enqueued", job_id=job_id)
                except Exception:
                    logger.debug("quality enqueue skipped", exc_info=True)

            log_event(
                logger,
                "job_process_done",
                job_id=job_id,
                model=model_name,
                segments=len(segments),
            )
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
            fresh = await self.db.get_job(job_id)
            retry_count = int((fresh or {}).get("retry_count") or 0)
            max_retries = int(
                (fresh or {}).get("max_retries")
                if fresh and fresh.get("max_retries") is not None
                else getattr(self.settings, "processing_max_retries", 2)
            )
            permanent = _is_permanent_failure(exc)
            if (
                not permanent
                and retry_count < max_retries
                and not (fresh or {}).get("cancel_requested")
            ):
                await self.db.update_job(
                    job_id,
                    status=JobStatus.QUEUED.value,
                    progress_message=f"Re-queued after failure ({retry_count + 1}/{max_retries})",
                    error_message=err,
                    retry_count=retry_count + 1,
                    worker_id=None,
                    processing_stage="queued",
                    started_at=None,
                    completed_at=None,
                    cpu_percent=None,
                )
            else:
                await self.db.update_job(
                    job_id,
                    status=JobStatus.FAILED.value,
                    progress_message="Failed",
                    cpu_percent=None,
                    error_message=err,
                    completed_at=utc_now(),
                    processing_stage="failed",
                )
        finally:
            if self.settings.unload_model_after_job:
                try:
                    self.engine.unload_model()
                    log_event(logger, "model_unloaded_after_job", job_id=job_id)
                except Exception:
                    logger.debug("unload_model failed", exc_info=True)

    def _transcribe_with_optional_chunking(
        self,
        backend,
        *,
        upload_path: str,
        model_name: str,
        language: str,
        task: str,
        progress_callback,
        initial_prompt: str | None,
        job_id: str,
    ):
        """Whole-file by default; optional chunked path with whole-file fallback."""
        from app.services.smart_processing.chunking import (
            assemble_text,
            detect_silence_gaps,
            extract_audio_chunk,
            merge_chunk_segments,
            plan_fixed_chunks,
            plan_silence_aware_chunks,
        )

        def _once(path: str):
            return backend.transcribe(
                path,
                model_name=model_name,
                language=language,
                task=task,
                progress_callback=progress_callback,
                initial_prompt=initial_prompt,
            )

        enable = bool(getattr(self.settings, "processing_enable_chunking", False))
        chunk_sec = float(getattr(self.settings, "processing_chunk_seconds", 600) or 600)
        overlap = float(
            getattr(self.settings, "processing_chunk_overlap_seconds", 1.0) or 0.0
        )
        if not enable or chunk_sec <= 0:
            return _once(upload_path)

        duration = None
        try:
            import json
            import subprocess

            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    upload_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if proc.returncode == 0:
                duration = float(json.loads(proc.stdout).get("format", {}).get("duration"))
        except Exception:
            duration = None

        if duration is None or duration <= chunk_sec:
            return _once(upload_path)

        try:
            gaps = detect_silence_gaps(upload_path)
            chunks = (
                plan_silence_aware_chunks(
                    duration, gaps, chunk_sec=chunk_sec, overlap_sec=overlap
                )
                if gaps
                else plan_fixed_chunks(
                    duration, chunk_sec=chunk_sec, overlap_sec=overlap
                )
            )
            if len(chunks) <= 1:
                return _once(upload_path)

            chunk_dir = Path(self.settings.output_dir) / job_id / "chunks"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            chunk_results = []
            language_out = None
            for chunk in chunks:
                progress_callback(
                    f"Transcribing chunk {chunk.index + 1}/{len(chunks)}",
                    None,
                )
                dest = chunk_dir / f"chunk_{chunk.index:04d}.wav"
                extract_audio_chunk(upload_path, str(dest), chunk.start_sec, chunk.end_sec)
                part = _once(str(dest))
                compat = part.to_whisper_compat() if hasattr(part, "to_whisper_compat") else part
                language_out = language_out or compat.get("language")
                segs = list(compat.get("segments") or [])
                chunk_results.append((chunk, segs))
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass

            merged = merge_chunk_segments(chunk_results)
            text = assemble_text(merged)
            from app.inference.base import TranscriptionResult

            return TranscriptionResult(
                text=text,
                language=language_out,
                duration=duration,
                segments=merged,
                model_name=model_name,
                backend=getattr(backend, "backend_id", "") or "",
                metadata={"chunked": True, "chunk_count": len(chunks)},
            )
        except Exception:
            logger.warning(
                "Chunked transcription failed for %s — falling back to whole file",
                job_id,
                exc_info=True,
            )
            return _once(upload_path)


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_permanent_failure(exc: BaseException) -> bool:
    """Deterministic failures that must not be retried indefinitely."""
    if isinstance(exc, (FileNotFoundError, ValueError, PermissionError)):
        return True
    msg = str(exc).lower()
    if "unsupported model" in msg or "unknown inference backend" in msg:
        return True
    if "corrupted" in msg or "invalid media" in msg:
        return True
    if "faster-whisper is not installed" in msg:
        return True
    return False


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
