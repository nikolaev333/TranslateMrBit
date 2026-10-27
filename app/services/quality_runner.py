"""Background Quality Analyzer runner (no torch / no Whisper import)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.logging_setup import log_event
from app.services.quality_analyzer import run_quality_analysis
from app.services.transcript_segments import join_segment_texts

if TYPE_CHECKING:
    from app.config import Settings
    from app.database import Database

logger = logging.getLogger(__name__)


class QualityAnalysisWorker:
    """Processes queued quality_analysis_runs without competing poll storms.

    Uses an asyncio lock and on-demand wakeups so it does not continuously
    issue SQLite BEGIN IMMEDIATE against the shared API/worker connection.
    """

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._busy = asyncio.Lock()

    async def start(self) -> None:
        interrupted = await self.db.fail_interrupted_quality_runs()
        if interrupted:
            log_event(logger, "quality_runs_interrupted_failed", count=interrupted)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="quality-analysis-worker")
        log_event(logger, "quality_analysis_worker_started")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
        log_event(logger, "quality_analysis_worker_stopped")

    def notify(self) -> None:
        """Wake the worker after enqueueing a run."""
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._drain()
                self._wake.clear()
                # Wait for notify or stop; also a slow idle poll for recovery.
                wake_task = asyncio.create_task(self._wake.wait())
                stop_task = asyncio.create_task(self._stop.wait())
                done, pending = await asyncio.wait(
                    {wake_task, stop_task},
                    timeout=5.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Quality worker loop error")
                await asyncio.sleep(1)

    async def _drain(self) -> None:
        async with self._busy:
            while not self._stop.is_set():
                run = await self.db.claim_next_quality_run()
                if run is None:
                    return
                await self._process(run)

    async def _process(self, run: dict) -> None:
        run_id = run["id"]
        job_id = run["job_id"]
        log_event(logger, "quality_run_start", run_id=run_id, job_id=job_id)
        try:
            job = await self.db.get_job(job_id)
            if not job:
                await self.db.fail_quality_run(run_id, "Job not found")
                return

            segments = await self.db.list_segments(job_id)
            if not segments:
                text = job.get("transcription_text") or ""
                segments = [
                    {
                        "id": "full-text",
                        "position": 0,
                        "original_text": text,
                        "edited_text": job.get("reviewed_text") or text,
                    }
                ]
            full_text = join_segment_texts(segments, use_edited=True) or (
                job.get("transcription_text") or ""
            )

            glossary_matches: list = []
            try:
                from app.services.smart_vocabulary import (
                    analyze_transcription,
                    term_from_row,
                )

                rows = await self.db.list_vocabulary_entries(enabled=True, limit=500)
                terms = [term_from_row(r) for r in rows]
                analysis = analyze_transcription(
                    full_text, terms, language=job.get("language")
                )
                glossary_matches = [
                    {
                        "matched_text": m.matched_text,
                        "correct": m.correct,
                        "start": m.start,
                        "end": m.end,
                    }
                    for m in analysis.matches
                ]
            except Exception:
                glossary_matches = []

            include_audio = bool(run.get("include_audio", 1)) and bool(
                self.settings.quality_include_audio
            )

            result = await asyncio.to_thread(
                run_quality_analysis,
                segments=segments,
                full_text=full_text,
                expected_language=job.get("language") or job.get("detected_language"),
                output_json_path=job.get("output_json_path"),
                upload_path=job.get("upload_path") if include_audio else None,
                include_audio=include_audio,
                glossary_matches=glossary_matches,
            )
            payload = result.to_dict()
            await self.db.complete_quality_run(
                run_id, result=payload, warnings=payload["warnings"]
            )
            log_event(
                logger,
                "quality_run_done",
                run_id=run_id,
                job_id=job_id,
                warnings=len(payload["warnings"]),
                priority=payload["overall_priority"],
            )
        except Exception:
            await self.db.fail_quality_run(run_id, "analysis failed")
            log_event(logger, "quality_run_failed", run_id=run_id, job_id=job_id)
            logger.exception("Quality run %s failed", run_id)
