"""Async SQLite persistence for transcription jobs and Smart Vocabulary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from app.models import SCHEMA_SQL, JobStatus, SegmentsStatus
from app.services.smart_vocabulary import dump_alternatives, new_entry_id, parse_alternatives
from app.services.transcript_segments import new_segment_id


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_VOCABULARY_SEED: list[dict[str, Any]] = [
    {
        "correct_term": "RTP",
        "alternatives": ["ар ти пи", "R T P", "артипи", "артепи"],
        "language": "Bulgarian",
        "category": "iGaming",
        "description": "Return to Player",
    },
    {
        "correct_term": "Jackpot",
        "alternatives": ["джак пот", "джакпот", "джакпота"],
        "language": "Bulgarian",
        "category": "iGaming",
        "description": "Progressive or fixed jackpot prize",
    },
    {
        "correct_term": "Bet Limits",
        "alternatives": ["бет лимитс", "бет лимити", "бет лимит"],
        "language": "Bulgarian",
        "category": "Technical",
        "description": "Minimum and maximum stake limits",
    },
]


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
            ("reviewed_text", "ALTER TABLE jobs ADD COLUMN reviewed_text TEXT"),
            (
                "vocabulary_analysis_json",
                "ALTER TABLE jobs ADD COLUMN vocabulary_analysis_json TEXT",
            ),
            (
                "vocabulary_prompt_used",
                "ALTER TABLE jobs ADD COLUMN vocabulary_prompt_used TEXT",
            ),
            (
                "reference_transcript",
                "ALTER TABLE jobs ADD COLUMN reference_transcript TEXT",
            ),
            (
                "suggestions_accepted",
                "ALTER TABLE jobs ADD COLUMN suggestions_accepted INTEGER DEFAULT 0",
            ),
            (
                "suggestions_rejected",
                "ALTER TABLE jobs ADD COLUMN suggestions_rejected INTEGER DEFAULT 0",
            ),
            (
                "segments_status",
                "ALTER TABLE jobs ADD COLUMN segments_status TEXT DEFAULT 'pending'",
            ),
            (
                "transcript_revision",
                "ALTER TABLE jobs ADD COLUMN transcript_revision INTEGER DEFAULT 0",
            ),
            (
                "has_word_timestamps",
                "ALTER TABLE jobs ADD COLUMN has_word_timestamps INTEGER DEFAULT 0",
            ),
            ("priority", "ALTER TABLE jobs ADD COLUMN priority INTEGER DEFAULT 100"),
            (
                "inference_backend",
                "ALTER TABLE jobs ADD COLUMN inference_backend TEXT DEFAULT 'openai-whisper'",
            ),
            ("retry_count", "ALTER TABLE jobs ADD COLUMN retry_count INTEGER DEFAULT 0"),
            ("max_retries", "ALTER TABLE jobs ADD COLUMN max_retries INTEGER DEFAULT 2"),
            (
                "cancel_requested",
                "ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER DEFAULT 0",
            ),
            ("worker_id", "ALTER TABLE jobs ADD COLUMN worker_id TEXT"),
            ("lease_expires_at", "ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT"),
            ("processing_stage", "ALTER TABLE jobs ADD COLUMN processing_stage TEXT"),
            ("queue_wait_seconds", "ALTER TABLE jobs ADD COLUMN queue_wait_seconds REAL"),
            ("model_load_seconds", "ALTER TABLE jobs ADD COLUMN model_load_seconds REAL"),
            ("inference_seconds", "ALTER TABLE jobs ADD COLUMN inference_seconds REAL"),
            ("finalize_seconds", "ALTER TABLE jobs ADD COLUMN finalize_seconds REAL"),
            ("peak_memory_bytes", "ALTER TABLE jobs ADD COLUMN peak_memory_bytes INTEGER"),
            ("realtime_factor", "ALTER TABLE jobs ADD COLUMN realtime_factor REAL"),
            (
                "allow_unsafe_model",
                "ALTER TABLE jobs ADD COLUMN allow_unsafe_model INTEGER DEFAULT 0",
            ),
        ]
        for name, sql in migrations:
            if name not in existing:
                await self.conn.execute(sql)

    async def seed_default_vocabulary(self) -> int:
        """Insert demo terms only when the vocabulary table is empty."""
        cursor = await self.conn.execute("SELECT COUNT(*) AS c FROM vocabulary_entries")
        row = await cursor.fetchone()
        if row and int(row["c"]) > 0:
            return 0
        inserted = 0
        for item in DEFAULT_VOCABULARY_SEED:
            await self.create_vocabulary_entry(item)
            inserted += 1
        return inserted

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
            "reviewed_text": None,
            "vocabulary_analysis_json": None,
            "vocabulary_prompt_used": None,
            "reference_transcript": None,
            "suggestions_accepted": 0,
            "suggestions_rejected": 0,
            "segments_status": SegmentsStatus.PENDING.value,
            "transcript_revision": 0,
            "has_word_timestamps": 0,
            "priority": int(job.get("priority") if job.get("priority") is not None else 100),
            "inference_backend": job.get("inference_backend") or "openai-whisper",
            "retry_count": 0,
            "max_retries": int(
                job["max_retries"] if job.get("max_retries") is not None else 2
            ),
            "cancel_requested": 0,
            "worker_id": None,
            "lease_expires_at": None,
            "processing_stage": None,
            "queue_wait_seconds": None,
            "model_load_seconds": None,
            "inference_seconds": None,
            "finalize_seconds": None,
            "peak_memory_bytes": None,
            "realtime_factor": None,
            "allow_unsafe_model": 1 if job.get("allow_unsafe_model") else 0,
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

    async def claim_next_job(
        self,
        *,
        worker_id: str | None = None,
        max_concurrent: int = 1,
    ) -> dict[str, Any] | None:
        """Atomically claim the highest-priority queued job.

        Uses BEGIN IMMEDIATE so workers sharing one SQLite DB cannot double-claim.
        This is not a multi-host distributed queue.
        """
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self.conn.execute(
                """
                SELECT COUNT(*) AS c FROM jobs
                WHERE status IN (?, ?, ?)
                """,
                (
                    JobStatus.PROCESSING.value,
                    JobStatus.PREPARING.value,
                    JobStatus.FINALIZING.value,
                ),
            )
            row = await cursor.fetchone()
            if row and int(row["c"]) >= max(1, max_concurrent):
                await self.conn.commit()
                return None

            cursor = await self.conn.execute(
                """
                SELECT * FROM jobs
                WHERE status IN (?, ?)
                  AND COALESCE(cancel_requested, 0) = 0
                ORDER BY COALESCE(priority, 100) DESC, created_at ASC
                LIMIT 1
                """,
                (JobStatus.QUEUED.value, JobStatus.WAITING_FOR_RESOURCES.value),
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
                    cpu_percent = ?, started_at = COALESCE(started_at, ?),
                    updated_at = ?, worker_id = ?, processing_stage = ?
                WHERE id = ?
                """,
                (
                    JobStatus.PREPARING.value,
                    "Preparing transcription",
                    1.0,
                    None,
                    now,
                    now,
                    worker_id,
                    "preparing",
                    job["id"],
                ),
            )
            await self.conn.commit()
            job["status"] = JobStatus.PREPARING.value
            job["progress_message"] = "Preparing transcription"
            job["progress_percent"] = 1.0
            job["cpu_percent"] = None
            job["started_at"] = job.get("started_at") or now
            job["updated_at"] = now
            job["worker_id"] = worker_id
            job["processing_stage"] = "preparing"
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
        # Also fail preparing/finalizing left after crash
        cursor2 = await self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, progress_message = ?, error_message = ?,
                completed_at = ?, updated_at = ?, worker_id = NULL
            WHERE status IN (?, ?)
            """,
            (
                JobStatus.FAILED.value,
                "Failed",
                "Interrupted by application restart. Re-submit the job if needed.",
                now,
                now,
                JobStatus.PREPARING.value,
                JobStatus.FINALIZING.value,
            ),
        )
        await self.conn.commit()
        return int(cursor.rowcount or 0) + int(cursor2.rowcount or 0)

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

    # ---- Smart Vocabulary CRUD -------------------------------------------------

    def _vocab_row(self, row: aiosqlite.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["alternatives"] = parse_alternatives(data.get("alternatives"))
        data["enabled"] = bool(data.get("enabled", 1))
        return data

    async def create_vocabulary_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        entry_id = str(payload.get("id") or new_entry_id())
        fields = {
            "id": entry_id,
            "correct_term": str(payload["correct_term"]).strip(),
            "alternatives": dump_alternatives(payload.get("alternatives") or []),
            "language": str(payload.get("language") or "any").strip() or "any",
            "category": str(payload.get("category") or "General").strip() or "General",
            "description": str(payload.get("description") or "").strip(),
            "enabled": 1 if payload.get("enabled", True) else 0,
            "created_at": now,
            "updated_at": now,
        }
        if not fields["correct_term"]:
            raise ValueError("correct_term is required")
        columns = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        await self.conn.execute(
            f"INSERT INTO vocabulary_entries ({columns}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        await self.conn.commit()
        return self._vocab_row(fields)

    async def get_vocabulary_entry(self, entry_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM vocabulary_entries WHERE id = ?",
            (entry_id,),
        )
        row = await cursor.fetchone()
        return self._vocab_row(row) if row else None

    async def list_vocabulary_entries(
        self,
        *,
        language: str | None = None,
        category: str | None = None,
        enabled: bool | None = None,
        search: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if language:
            clauses.append("LOWER(language) = LOWER(?)")
            params.append(language.strip())
        if category:
            clauses.append("LOWER(category) = LOWER(?)")
            params.append(category.strip())
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(1 if enabled else 0)
        if search:
            like = f"%{search.strip()}%"
            clauses.append(
                "(correct_term LIKE ? OR alternatives LIKE ? OR description LIKE ? OR category LIKE ?)"
            )
            params.extend([like, like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        cursor = await self.conn.execute(
            f"""
            SELECT * FROM vocabulary_entries
            {where}
            ORDER BY category ASC, correct_term ASC
            LIMIT ? OFFSET ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [self._vocab_row(r) for r in rows]

    async def count_vocabulary_entries(
        self,
        *,
        language: str | None = None,
        category: str | None = None,
        enabled: bool | None = None,
        search: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if language:
            clauses.append("LOWER(language) = LOWER(?)")
            params.append(language.strip())
        if category:
            clauses.append("LOWER(category) = LOWER(?)")
            params.append(category.strip())
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(1 if enabled else 0)
        if search:
            like = f"%{search.strip()}%"
            clauses.append(
                "(correct_term LIKE ? OR alternatives LIKE ? OR description LIKE ? OR category LIKE ?)"
            )
            params.extend([like, like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self.conn.execute(
            f"SELECT COUNT(*) AS c FROM vocabulary_entries {where}",
            params,
        )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def update_vocabulary_entry(
        self, entry_id: str, **fields: Any
    ) -> dict[str, Any] | None:
        if not fields:
            return await self.get_vocabulary_entry(entry_id)
        allowed = {
            "correct_term",
            "alternatives",
            "language",
            "category",
            "description",
            "enabled",
        }
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "correct_term":
                updates[key] = str(value).strip()
            elif key == "alternatives":
                updates[key] = dump_alternatives(value or [])
            elif key == "enabled":
                updates[key] = 1 if value else 0
            else:
                updates[key] = str(value).strip() if value is not None else ""
        if not updates:
            return await self.get_vocabulary_entry(entry_id)
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [entry_id]
        await self.conn.execute(
            f"UPDATE vocabulary_entries SET {assignments} WHERE id = ?",
            values,
        )
        await self.conn.commit()
        return await self.get_vocabulary_entry(entry_id)

    async def delete_vocabulary_entry(self, entry_id: str) -> dict[str, Any] | None:
        entry = await self.get_vocabulary_entry(entry_id)
        if not entry:
            return None
        await self.conn.execute(
            "DELETE FROM vocabulary_entries WHERE id = ?",
            (entry_id,),
        )
        await self.conn.commit()
        return entry

    async def list_vocabulary_categories(self) -> list[str]:
        cursor = await self.conn.execute(
            """
            SELECT DISTINCT category FROM vocabulary_entries
            ORDER BY category ASC
            """
        )
        rows = await cursor.fetchall()
        return [str(r["category"]) for r in rows if r["category"]]

    async def suggestion_metrics_totals(self) -> dict[str, int]:
        cursor = await self.conn.execute(
            """
            SELECT
              COALESCE(SUM(suggestions_accepted), 0) AS accepted,
              COALESCE(SUM(suggestions_rejected), 0) AS rejected
            FROM jobs
            """
        )
        row = await cursor.fetchone()
        return {
            "accepted": int(row["accepted"] or 0) if row else 0,
            "rejected": int(row["rejected"] or 0) if row else 0,
        }

    # ---- Transcript segments / revisions (Transcription Studio) ------------

    def _segment_row(self, row: aiosqlite.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["is_edited"] = bool(data.get("is_edited", 0))
        data["position"] = int(data.get("position") or 0)
        data["start_sec"] = float(data["start_sec"])
        data["end_sec"] = float(data["end_sec"])
        return data

    async def replace_job_segments(
        self,
        job_id: str,
        segments: list[dict[str, Any]],
        *,
        has_word_timestamps: bool = False,
        status: str = SegmentsStatus.READY.value,
    ) -> list[dict[str, Any]]:
        """Replace all segments for a job in one transaction."""
        now = utc_now()
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            await self.conn.execute(
                "DELETE FROM transcript_segments WHERE job_id = ?",
                (job_id,),
            )
            stored: list[dict[str, Any]] = []
            for seg in segments:
                fields = {
                    "id": seg.get("id") or new_segment_id(),
                    "job_id": job_id,
                    "position": int(seg["position"]),
                    "start_sec": float(seg["start_sec"]),
                    "end_sec": float(seg["end_sec"]),
                    "original_text": str(seg.get("original_text") or ""),
                    "edited_text": str(seg.get("edited_text") or seg.get("original_text") or ""),
                    "is_edited": 1 if seg.get("is_edited") else 0,
                    "words_json": seg.get("words_json"),
                    "edit_source": seg.get("edit_source"),
                    "updated_at": now,
                }
                cols = ", ".join(fields.keys())
                placeholders = ", ".join("?" for _ in fields)
                await self.conn.execute(
                    f"INSERT INTO transcript_segments ({cols}) VALUES ({placeholders})",
                    tuple(fields.values()),
                )
                stored.append(self._segment_row(fields))
            await self.conn.execute(
                """
                UPDATE jobs
                SET segments_status = ?, has_word_timestamps = ?,
                    transcript_revision = 0, updated_at = ?
                WHERE id = ?
                """,
                (status, 1 if has_word_timestamps else 0, now, job_id),
            )
            await self.conn.commit()
            return stored
        except Exception:
            await self.conn.rollback()
            raise

    async def list_segments(self, job_id: str) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM transcript_segments
            WHERE job_id = ?
            ORDER BY position ASC
            """,
            (job_id,),
        )
        rows = await cursor.fetchall()
        return [self._segment_row(r) for r in rows]

    async def get_segment(self, job_id: str, segment_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM transcript_segments
            WHERE job_id = ? AND id = ?
            """,
            (job_id, segment_id),
        )
        row = await cursor.fetchone()
        return self._segment_row(row) if row else None

    async def count_segments(self, job_id: str) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS c FROM transcript_segments WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def _bump_revision(
        self,
        job_id: str,
        *,
        expected_revision: int | None,
        actor: str = "session",
    ) -> int:
        job = await self.get_job(job_id)
        if not job:
            raise LookupError("Job not found")
        current = int(job.get("transcript_revision") or 0)
        if expected_revision is not None and expected_revision != current:
            raise ConflictError(
                f"Transcript revision conflict: expected {expected_revision}, current {current}"
            )
        new_rev = current + 1
        await self.conn.execute(
            """
            UPDATE jobs SET transcript_revision = ?, updated_at = ? WHERE id = ?
            """,
            (new_rev, utc_now(), job_id),
        )
        return new_rev

    async def add_revision(
        self,
        *,
        job_id: str,
        revision_no: int,
        action: str,
        segment_id: str | None = None,
        previous_text: str | None = None,
        new_text: str | None = None,
        actor: str = "session",
    ) -> dict[str, Any]:
        fields = {
            "id": new_segment_id(),
            "job_id": job_id,
            "segment_id": segment_id,
            "revision_no": revision_no,
            "previous_text": previous_text,
            "new_text": new_text,
            "action": action,
            "actor": actor,
            "created_at": utc_now(),
        }
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        await self.conn.execute(
            f"INSERT INTO transcript_revisions ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        return fields

    async def list_revisions(
        self, job_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM transcript_revisions
            WHERE job_id = ?
            ORDER BY revision_no DESC, created_at DESC
            LIMIT ? OFFSET ?
            """,
            (job_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_segment_text(
        self,
        job_id: str,
        segment_id: str,
        edited_text: str,
        *,
        expected_revision: int | None = None,
        edit_source: str | None = "manual",
        actor: str = "session",
    ) -> tuple[dict[str, Any], int]:
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            seg = await self.get_segment(job_id, segment_id)
            if not seg:
                await self.conn.rollback()
                raise LookupError("Segment not found")
            previous = seg.get("edited_text") or ""
            new_text = edited_text
            is_edited = 0 if new_text == (seg.get("original_text") or "") else 1
            source = None if not is_edited else edit_source
            now = utc_now()
            await self.conn.execute(
                """
                UPDATE transcript_segments
                SET edited_text = ?, is_edited = ?, edit_source = ?, updated_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (new_text, is_edited, source, now, segment_id, job_id),
            )
            rev = await self._bump_revision(
                job_id, expected_revision=expected_revision, actor=actor
            )
            await self.add_revision(
                job_id=job_id,
                revision_no=rev,
                action="edit" if is_edited else "restore",
                segment_id=segment_id,
                previous_text=previous,
                new_text=new_text,
                actor=actor,
            )
            await self.conn.commit()
            updated = await self.get_segment(job_id, segment_id)
            assert updated is not None
            return updated, rev
        except Exception:
            await self.conn.rollback()
            raise

    async def restore_segment(
        self,
        job_id: str,
        segment_id: str,
        *,
        expected_revision: int | None = None,
        actor: str = "session",
    ) -> tuple[dict[str, Any], int]:
        seg = await self.get_segment(job_id, segment_id)
        if not seg:
            raise LookupError("Segment not found")
        return await self.update_segment_text(
            job_id,
            segment_id,
            seg.get("original_text") or "",
            expected_revision=expected_revision,
            edit_source=None,
            actor=actor,
        )

    async def bulk_save_segments(
        self,
        job_id: str,
        changes: list[dict[str, Any]],
        *,
        expected_revision: int,
        actor: str = "session",
    ) -> tuple[list[dict[str, Any]], int]:
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            job = await self.get_job(job_id)
            if not job:
                raise LookupError("Job not found")
            current = int(job.get("transcript_revision") or 0)
            if expected_revision != current:
                raise ConflictError(
                    f"Transcript revision conflict: expected {expected_revision}, current {current}"
                )
            now = utc_now()
            for change in changes:
                sid = str(change["id"])
                seg = await self.get_segment(job_id, sid)
                if not seg:
                    raise LookupError(f"Segment not found: {sid}")
                previous = seg.get("edited_text") or ""
                new_text = str(change.get("edited_text") if "edited_text" in change else previous)
                if new_text == previous:
                    continue
                is_edited = 0 if new_text == (seg.get("original_text") or "") else 1
                source = change.get("edit_source")
                if is_edited and not source:
                    source = "manual"
                if not is_edited:
                    source = None
                await self.conn.execute(
                    """
                    UPDATE transcript_segments
                    SET edited_text = ?, is_edited = ?, edit_source = ?, updated_at = ?
                    WHERE id = ? AND job_id = ?
                    """,
                    (new_text, is_edited, source, now, sid, job_id),
                )
                await self.add_revision(
                    job_id=job_id,
                    revision_no=current + 1,
                    action="bulk",
                    segment_id=sid,
                    previous_text=previous,
                    new_text=new_text,
                    actor=actor,
                )
            new_rev = current + 1
            await self.conn.execute(
                """
                UPDATE jobs SET transcript_revision = ?, updated_at = ? WHERE id = ?
                """,
                (new_rev, now, job_id),
            )
            await self.conn.commit()
            return await self.list_segments(job_id), new_rev
        except Exception:
            await self.conn.rollback()
            raise

    async def split_segment(
        self,
        job_id: str,
        segment_id: str,
        split_at: float,
        *,
        left_text: str | None = None,
        right_text: str | None = None,
        expected_revision: int | None = None,
        actor: str = "session",
    ) -> tuple[list[dict[str, Any]], int]:
        from app.services.transcript_segments import split_text_roughly, validate_split_at

        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            seg = await self.get_segment(job_id, segment_id)
            if not seg:
                raise LookupError("Segment not found")
            validate_split_at(seg["start_sec"], seg["end_sec"], float(split_at))
            text = seg.get("edited_text") or ""
            if left_text is None or right_text is None:
                a, b = split_text_roughly(text)
            else:
                a, b = left_text, right_text
            pos = int(seg["position"])
            # Shift later segments
            await self.conn.execute(
                """
                UPDATE transcript_segments
                SET position = position + 1
                WHERE job_id = ? AND position > ?
                """,
                (job_id, pos),
            )
            now = utc_now()
            # Update left (same id)
            await self.conn.execute(
                """
                UPDATE transcript_segments
                SET end_sec = ?, edited_text = ?, original_text = ?,
                    is_edited = 1, edit_source = 'manual', words_json = NULL, updated_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (float(split_at), a, a, now, segment_id, job_id),
            )
            right_id = new_segment_id()
            await self.conn.execute(
                """
                INSERT INTO transcript_segments
                (id, job_id, position, start_sec, end_sec, original_text, edited_text,
                 is_edited, words_json, edit_source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL, 'manual', ?)
                """,
                (
                    right_id,
                    job_id,
                    pos + 1,
                    float(split_at),
                    float(seg["end_sec"]),
                    b,
                    b,
                    now,
                ),
            )
            rev = await self._bump_revision(
                job_id, expected_revision=expected_revision, actor=actor
            )
            await self.add_revision(
                job_id=job_id,
                revision_no=rev,
                action="split",
                segment_id=segment_id,
                previous_text=text,
                new_text=f"{a} | {b}",
                actor=actor,
            )
            await self.conn.commit()
            return await self.list_segments(job_id), rev
        except Exception:
            await self.conn.rollback()
            raise

    async def merge_segments(
        self,
        job_id: str,
        left_id: str,
        right_id: str,
        *,
        expected_revision: int | None = None,
        actor: str = "session",
    ) -> tuple[list[dict[str, Any]], int]:
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            left = await self.get_segment(job_id, left_id)
            right = await self.get_segment(job_id, right_id)
            if not left or not right:
                raise LookupError("Segment not found")
            if int(right["position"]) != int(left["position"]) + 1:
                raise ValueError("Only adjacent segments can be merged")
            merged_text = (
                f"{(left.get('edited_text') or '').strip()} {(right.get('edited_text') or '').strip()}"
            ).strip()
            orig = (
                f"{(left.get('original_text') or '').strip()} {(right.get('original_text') or '').strip()}"
            ).strip()
            now = utc_now()
            await self.conn.execute(
                """
                UPDATE transcript_segments
                SET end_sec = ?, edited_text = ?, original_text = ?,
                    is_edited = ?, edit_source = ?, words_json = NULL, updated_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (
                    float(right["end_sec"]),
                    merged_text,
                    orig,
                    0 if merged_text == orig else 1,
                    "manual" if merged_text != orig else None,
                    now,
                    left_id,
                    job_id,
                ),
            )
            await self.conn.execute(
                "DELETE FROM transcript_segments WHERE id = ? AND job_id = ?",
                (right_id, job_id),
            )
            await self.conn.execute(
                """
                UPDATE transcript_segments
                SET position = position - 1
                WHERE job_id = ? AND position > ?
                """,
                (job_id, int(left["position"])),
            )
            rev = await self._bump_revision(
                job_id, expected_revision=expected_revision, actor=actor
            )
            await self.add_revision(
                job_id=job_id,
                revision_no=rev,
                action="merge",
                segment_id=left_id,
                previous_text=f"{left.get('edited_text')} || {right.get('edited_text')}",
                new_text=merged_text,
                actor=actor,
            )
            await self.conn.commit()
            return await self.list_segments(job_id), rev
        except Exception:
            await self.conn.rollback()
            raise

    # ---- Quality Analyzer ----------------------------------------------------

    async def create_quality_run(self, job_id: str, *, include_audio: bool = True) -> dict[str, Any]:
        from app.services.transcript_segments import new_segment_id

        now = utc_now()
        fields = {
            "id": new_segment_id(),
            "job_id": job_id,
            "status": "queued",
            "analysis_version": None,
            "include_audio": 1 if include_audio else 0,
            "overall_priority": None,
            "segments_analyzed": 0,
            "warnings_count": 0,
            "config_json": None,
            "result_json": None,
            "error_message": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "updated_at": now,
        }
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        await self.conn.execute(
            f"INSERT INTO quality_analysis_runs ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        await self.conn.commit()
        return fields

    async def get_quality_run(self, run_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM quality_analysis_runs WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_latest_quality_run(self, job_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM quality_analysis_runs
            WHERE job_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (job_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def claim_next_quality_run(self) -> dict[str, Any] | None:
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self.conn.execute(
                """
                SELECT * FROM quality_analysis_runs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if not row:
                await self.conn.commit()
                return None
            run = dict(row)
            now = utc_now()
            await self.conn.execute(
                """
                UPDATE quality_analysis_runs
                SET status = 'running', started_at = ?, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, run["id"]),
            )
            await self.conn.commit()
            run["status"] = "running"
            run["started_at"] = now
            run["updated_at"] = now
            return run
        except Exception:
            await self.conn.rollback()
            raise

    async def fail_interrupted_quality_runs(self) -> int:
        now = utc_now()
        cursor = await self.conn.execute(
            """
            UPDATE quality_analysis_runs
            SET status = 'failed',
                error_message = 'Interrupted by application restart. Re-run analysis.',
                completed_at = ?, updated_at = ?
            WHERE status = 'running'
            """,
            (now, now),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def complete_quality_run(
        self,
        run_id: str,
        *,
        result: dict[str, Any],
        warnings: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        from app.services.transcript_segments import new_segment_id

        now = utc_now()
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            run = await self.get_quality_run(run_id)
            if not run:
                await self.conn.rollback()
                return None
            job_id = run["job_id"]
            await self.conn.execute(
                "DELETE FROM quality_warnings WHERE run_id = ?", (run_id,)
            )
            for w in warnings:
                fields = {
                    "id": new_segment_id(),
                    "run_id": run_id,
                    "job_id": job_id,
                    "rule_id": w["rule_id"],
                    "description": w.get("description") or "",
                    "priority": w.get("priority") or "LOW",
                    "explanation": w.get("explanation") or "",
                    "evidence_json": json.dumps(w.get("evidence") or {}, ensure_ascii=False),
                    "segment_ids_json": json.dumps(w.get("segment_ids") or [], ensure_ascii=False),
                    "start_sec": w.get("start_sec"),
                    "end_sec": w.get("end_sec"),
                    "review_status": "open",
                    "reviewer_note": None,
                    "reviewed_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
                cols = ", ".join(fields.keys())
                placeholders = ", ".join("?" for _ in fields)
                await self.conn.execute(
                    f"INSERT INTO quality_warnings ({cols}) VALUES ({placeholders})",
                    tuple(fields.values()),
                )
            await self.conn.execute(
                """
                UPDATE quality_analysis_runs
                SET status = 'completed',
                    analysis_version = ?,
                    overall_priority = ?,
                    segments_analyzed = ?,
                    warnings_count = ?,
                    config_json = ?,
                    result_json = ?,
                    error_message = NULL,
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    result.get("analysis_version"),
                    result.get("overall_priority"),
                    int(result.get("segments_analyzed") or 0),
                    len(warnings),
                    json.dumps(result.get("config_snapshot") or {}, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    now,
                    now,
                    run_id,
                ),
            )
            await self.conn.commit()
            return await self.get_quality_run(run_id)
        except Exception:
            await self.conn.rollback()
            raise

    async def fail_quality_run(self, run_id: str, error_message: str) -> None:
        now = utc_now()
        msg = (error_message or "Analysis failed")[:400]
        await self.conn.execute(
            """
            UPDATE quality_analysis_runs
            SET status = 'failed', error_message = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (msg, now, now, run_id),
        )
        await self.conn.commit()

    async def list_quality_warnings(
        self,
        job_id: str,
        *,
        run_id: str | None = None,
        review_status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["job_id = ?"]
        params: list[Any] = [job_id]
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if review_status:
            clauses.append("review_status = ?")
            params.append(review_status)
        where = " AND ".join(clauses)
        cursor = await self.conn.execute(
            f"""
            SELECT * FROM quality_warnings
            WHERE {where}
            ORDER BY
              CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 WHEN 'LOW' THEN 2 ELSE 3 END,
              CASE WHEN start_sec IS NULL THEN 1 ELSE 0 END,
              start_sec ASC,
              created_at ASC
            """,
            params,
        )
        rows = await cursor.fetchall()
        out = []
        for r in rows:
            data = dict(r)
            try:
                data["evidence"] = json.loads(data.get("evidence_json") or "{}")
            except json.JSONDecodeError:
                data["evidence"] = {}
            try:
                data["segment_ids"] = json.loads(data.get("segment_ids_json") or "[]")
            except json.JSONDecodeError:
                data["segment_ids"] = []
            out.append(data)
        return out

    async def get_quality_warning(self, job_id: str, warning_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM quality_warnings WHERE job_id = ? AND id = ?",
            (job_id, warning_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["evidence"] = json.loads(data.get("evidence_json") or "{}")
        except json.JSONDecodeError:
            data["evidence"] = {}
        try:
            data["segment_ids"] = json.loads(data.get("segment_ids_json") or "[]")
        except json.JSONDecodeError:
            data["segment_ids"] = []
        return data

    async def update_quality_warning_review(
        self,
        job_id: str,
        warning_id: str,
        *,
        review_status: str,
        reviewer_note: str | None = None,
        actor: str = "session",
    ) -> dict[str, Any] | None:
        from app.services.transcript_segments import new_segment_id

        warning = await self.get_quality_warning(job_id, warning_id)
        if not warning:
            return None
        now = utc_now()
        await self.conn.execute(
            """
            UPDATE quality_warnings
            SET review_status = ?, reviewer_note = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ? AND job_id = ?
            """,
            (review_status, reviewer_note, now, now, warning_id, job_id),
        )
        await self.conn.execute(
            """
            INSERT INTO quality_reviews (id, warning_id, job_id, action, note, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (new_segment_id(), warning_id, job_id, review_status, reviewer_note, actor, now),
        )
        await self.conn.commit()
        return await self.get_quality_warning(job_id, warning_id)

    async def save_reference_evaluation(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.transcript_segments import new_segment_id

        fields = {
            "id": new_segment_id(),
            "job_id": payload["job_id"],
            "run_id": payload.get("run_id"),
            "compare_against": payload["compare_against"],
            "reference_text": payload["reference_text"],
            "hypothesis_text": payload["hypothesis_text"],
            "wer": payload.get("wer"),
            "cer": payload.get("cer"),
            "substitutions": payload.get("substitutions"),
            "deletions": payload.get("deletions"),
            "insertions": payload.get("insertions"),
            "reference_words": payload.get("reference_words"),
            "hypothesis_words": payload.get("hypothesis_words"),
            "created_at": utc_now(),
        }
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        await self.conn.execute(
            f"INSERT INTO quality_reference_evaluations ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        await self.conn.commit()
        return fields

    async def list_reference_evaluations(self, job_id: str) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM quality_reference_evaluations
            WHERE job_id = ?
            ORDER BY created_at DESC
            """,
            (job_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]




    async def list_queue_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN (?, ?, ?, ?, ?, ?)
            ORDER BY COALESCE(priority, 100) DESC, created_at ASC
            LIMIT ?
            """,
            (
                JobStatus.QUEUED.value,
                JobStatus.WAITING_FOR_RESOURCES.value,
                JobStatus.PREPARING.value,
                JobStatus.PROCESSING.value,
                JobStatus.FINALIZING.value,
                JobStatus.FAILED.value,
                limit,
            ),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def count_active_jobs(self) -> int:
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM jobs
            WHERE status IN (?, ?, ?)
            """,
            (
                JobStatus.PROCESSING.value,
                JobStatus.PREPARING.value,
                JobStatus.FINALIZING.value,
            ),
        )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def request_job_cancel(self, job_id: str) -> dict[str, Any] | None:
        job = await self.get_job(job_id)
        if not job:
            return None
        status = job.get("status")
        if status in {JobStatus.COMPLETED.value, JobStatus.CANCELLED.value}:
            return job
        if status in {JobStatus.QUEUED.value, JobStatus.WAITING_FOR_RESOURCES.value}:
            return await self.update_job(
                job_id,
                status=JobStatus.CANCELLED.value,
                cancel_requested=1,
                progress_message="Cancelled",
                completed_at=utc_now(),
            )
        return await self.update_job(
            job_id,
            cancel_requested=1,
            progress_message="Cancellation requested",
        )

    async def retry_job(self, job_id: str) -> dict[str, Any] | None:
        job = await self.get_job(job_id)
        if not job:
            return None
        if job.get("status") not in {JobStatus.FAILED.value, JobStatus.CANCELLED.value}:
            raise ValueError("Only failed or cancelled jobs can be retried")
        retries = int(job.get("retry_count") or 0) + 1
        max_r = int(job.get("max_retries") or 2)
        if retries > max_r:
            raise ValueError("Retry limit exhausted")
        return await self.update_job(
            job_id,
            status=JobStatus.QUEUED.value,
            retry_count=retries,
            cancel_requested=0,
            error_message=None,
            progress_message="Re-queued for retry",
            progress_percent=0.0,
            processing_stage="queued",
            worker_id=None,
            started_at=None,
            completed_at=None,
        )

    async def upsert_worker_heartbeat(
        self,
        worker_id: str,
        *,
        status: str = "idle",
        current_job_id: str | None = None,
        hostname: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        await self.conn.execute(
            """
            INSERT INTO worker_heartbeats (worker_id, hostname, status, current_job_id, last_seen_at, meta_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
              hostname=excluded.hostname,
              status=excluded.status,
              current_job_id=excluded.current_job_id,
              last_seen_at=excluded.last_seen_at,
              meta_json=excluded.meta_json
            """,
            (
                worker_id,
                hostname,
                status,
                current_job_id,
                now,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        await self.conn.commit()

    async def list_worker_heartbeats(self) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM worker_heartbeats ORDER BY last_seen_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def save_processing_metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.transcript_segments import new_segment_id

        fields = {
            "id": new_segment_id(),
            "job_id": payload["job_id"],
            "model_name": payload.get("model_name"),
            "backend": payload.get("backend"),
            "audio_duration_sec": payload.get("audio_duration_sec"),
            "queue_wait_sec": payload.get("queue_wait_sec"),
            "model_load_sec": payload.get("model_load_sec"),
            "inference_sec": payload.get("inference_sec"),
            "finalize_sec": payload.get("finalize_sec"),
            "total_sec": payload.get("total_sec"),
            "peak_memory_bytes": payload.get("peak_memory_bytes"),
            "realtime_factor": payload.get("realtime_factor"),
            "created_at": utc_now(),
        }
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        await self.conn.execute(
            f"INSERT INTO processing_metrics ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        await self.conn.commit()
        return fields

    async def list_processing_metrics(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM processing_metrics
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


class ConflictError(Exception):
    """Optimistic concurrency conflict for transcript edits."""


def serialize_segments(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
