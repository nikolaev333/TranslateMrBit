"""Async SQLite persistence for transcription jobs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from app.models import SCHEMA_SQL, JobStatus


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(SCHEMA_SQL)
        await self._ensure_columns()
        await self._conn.commit()

    async def _ensure_columns(self) -> None:
        """Add columns introduced after the initial schema (existing prod DBs)."""
        cursor = await self.conn.execute("PRAGMA table_info(jobs)")
        rows = await cursor.fetchall()
        existing = {row["name"] if isinstance(row, aiosqlite.Row) else row[1] for row in rows}
        migrations = [
            ("progress_percent", "ALTER TABLE jobs ADD COLUMN progress_percent REAL"),
            ("cpu_percent", "ALTER TABLE jobs ADD COLUMN cpu_percent REAL"),
        ]
        for name, sql in migrations:
            if name not in existing:
                await self.conn.execute(sql)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        fields = {
            "id": job["id"],
            "original_filename": job["original_filename"],
            "stored_filename": job["stored_filename"],
            "upload_path": job["upload_path"],
            "file_size": job["file_size"],
            "content_type": job.get("content_type"),
            "model_name": job["model_name"],
            "language": job["language"],
            "task": job.get("task", "transcribe"),
            "status": JobStatus.QUEUED.value,
            "progress_message": "Queued for transcription",
            "progress_percent": 0.0,
            "cpu_percent": None,
            "error_message": None,
            "transcription_text": None,
            "detected_language": None,
            "duration_seconds": None,
            "output_txt_path": None,
            "output_srt_path": None,
            "output_vtt_path": None,
            "output_json_path": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "updated_at": now,
        }
        columns = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        await self.conn.execute(
            f"INSERT INTO jobs ({columns}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        await self.conn.commit()
        return fields

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_jobs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM jobs
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def count_jobs(self) -> int:
        cursor = await self.conn.execute("SELECT COUNT(*) AS c FROM jobs")
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def update_job(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return await self.get_job(job_id)
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [job_id]
        await self.conn.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            values,
        )
        await self.conn.commit()
        return await self.get_job(job_id)

    async def delete_job(self, job_id: str) -> dict[str, Any] | None:
        job = await self.get_job(job_id)
        if not job:
            return None
        await self.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await self.conn.commit()
        return job

    async def claim_next_job(self) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job if no job is processing."""
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self.conn.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status = ?",
                (JobStatus.PROCESSING.value,),
            )
            row = await cursor.fetchone()
            if row and int(row["c"]) > 0:
                await self.conn.commit()
                return None

            cursor = await self.conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (JobStatus.QUEUED.value,),
            )
            job_row = await cursor.fetchone()
            if not job_row:
                await self.conn.commit()
                return None

            job = dict(job_row)
            now = utc_now()
            await self.conn.execute(
                """
                UPDATE jobs
                SET status = ?, progress_message = ?, progress_percent = ?,
                    cpu_percent = ?, started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    JobStatus.PROCESSING.value,
                    "Loading model / starting transcription",
                    1.0,
                    None,
                    now,
                    now,
                    job["id"],
                ),
            )
            await self.conn.commit()
            job["status"] = JobStatus.PROCESSING.value
            job["progress_message"] = "Loading model / starting transcription"
            job["progress_percent"] = 1.0
            job["cpu_percent"] = None
            job["started_at"] = now
            job["updated_at"] = now
            return job
        except Exception:
            await self.conn.rollback()
            raise

    async def requeue_interrupted_jobs(self) -> int:
        """Fail jobs left in processing after a crash.

        Re-queueing can death-loop small VPS hosts when the interrupted job was
        loading a large Whisper model into RAM. Operators can re-submit the job.
        """
        now = utc_now()
        cursor = await self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, progress_message = ?, error_message = ?,
                completed_at = ?, updated_at = ?
            WHERE status = ?
            """,
            (
                JobStatus.FAILED.value,
                "Failed",
                "Interrupted by application restart. Re-submit the job if needed.",
                now,
                now,
                JobStatus.PROCESSING.value,
            ),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def list_old_uploads(self, cutoff_iso: str) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """
            SELECT id, upload_path, status, completed_at, created_at
            FROM jobs
            WHERE status IN (?, ?)
              AND upload_path IS NOT NULL
              AND (
                    (completed_at IS NOT NULL AND completed_at < ?)
                 OR (completed_at IS NULL AND created_at < ?)
              )
            """,
            (
                JobStatus.COMPLETED.value,
                JobStatus.FAILED.value,
                cutoff_iso,
                cutoff_iso,
            ),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def clear_upload_path(self, job_id: str) -> None:
        await self.update_job(job_id, upload_path="")


def serialize_segments(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
