"""Periodic cleanup of old uploaded source audio files."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.database import Database
from app.media import safe_unlink

logger = logging.getLogger(__name__)


class UploadCleanupTask:
    def __init__(
        self,
        db: Database,
        *,
        auto_delete_days: int,
        interval_seconds: float = 3600,
    ) -> None:
        self.db = db
        self.auto_delete_days = auto_delete_days
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self.auto_delete_days <= 0:
            logger.info("Upload auto-cleanup disabled")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="upload-cleanup")
        logger.info(
            "Upload cleanup enabled (delete after %s days)",
            self.auto_delete_days,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Upload cleanup failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> int:
        if self.auto_delete_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.auto_delete_days)
        jobs = await self.db.list_old_uploads(cutoff.isoformat())
        deleted = 0
        for job in jobs:
            path = job.get("upload_path")
            if not path:
                continue
            p = Path(path)
            if p.is_file():
                safe_unlink(p)
                deleted += 1
            await self.db.clear_upload_path(job["id"])
        if deleted:
            logger.info("Deleted %s old upload file(s)", deleted)
        return deleted
