"""SQLAlchemy-free SQLite schema helpers and job status constants."""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    upload_path TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    content_type TEXT,
    model_name TEXT NOT NULL,
    language TEXT NOT NULL,
    task TEXT NOT NULL DEFAULT 'transcribe',
    status TEXT NOT NULL,
    progress_message TEXT,
    progress_percent REAL,
    cpu_percent REAL,
    error_message TEXT,
    transcription_text TEXT,
    detected_language TEXT,
    duration_seconds REAL,
    output_txt_path TEXT,
    output_srt_path TEXT,
    output_vtt_path TEXT,
    output_json_path TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
"""
