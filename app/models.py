"""SQLAlchemy-free SQLite schema helpers and job status constants."""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    WAITING_FOR_RESOURCES = "waiting_for_resources"
    PREPARING = "preparing"
    PROCESSING = "processing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SegmentsStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    UNSUPPORTED = "unsupported"


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
    updated_at TEXT NOT NULL,
    reviewed_text TEXT,
    vocabulary_analysis_json TEXT,
    vocabulary_prompt_used TEXT,
    reference_transcript TEXT,
    suggestions_accepted INTEGER DEFAULT 0,
    suggestions_rejected INTEGER DEFAULT 0,
    segments_status TEXT DEFAULT 'pending',
    transcript_revision INTEGER DEFAULT 0,
    has_word_timestamps INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 100,
    inference_backend TEXT DEFAULT 'openai-whisper',
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 2,
    cancel_requested INTEGER DEFAULT 0,
    worker_id TEXT,
    lease_expires_at TEXT,
    processing_stage TEXT,
    queue_wait_seconds REAL,
    model_load_seconds REAL,
    inference_seconds REAL,
    finalize_seconds REAL,
    peak_memory_bytes INTEGER,
    realtime_factor REAL,
    allow_unsafe_model INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS vocabulary_entries (
    id TEXT PRIMARY KEY,
    correct_term TEXT NOT NULL,
    alternatives TEXT NOT NULL DEFAULT '[]',
    language TEXT NOT NULL DEFAULT 'any',
    category TEXT NOT NULL DEFAULT 'General',
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vocab_language ON vocabulary_entries(language);
CREATE INDEX IF NOT EXISTS idx_vocab_category ON vocabulary_entries(category);
CREATE INDEX IF NOT EXISTS idx_vocab_enabled ON vocabulary_entries(enabled);
CREATE INDEX IF NOT EXISTS idx_vocab_correct ON vocabulary_entries(correct_term);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    original_text TEXT NOT NULL DEFAULT '',
    edited_text TEXT NOT NULL DEFAULT '',
    is_edited INTEGER NOT NULL DEFAULT 0,
    words_json TEXT,
    edit_source TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_segments_job ON transcript_segments(job_id);
CREATE INDEX IF NOT EXISTS idx_segments_job_pos ON transcript_segments(job_id, position);

CREATE TABLE IF NOT EXISTS transcript_revisions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    segment_id TEXT,
    revision_no INTEGER NOT NULL,
    previous_text TEXT,
    new_text TEXT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'session',
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_revisions_job ON transcript_revisions(job_id, revision_no DESC);

CREATE TABLE IF NOT EXISTS quality_analysis_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    analysis_version TEXT,
    include_audio INTEGER NOT NULL DEFAULT 1,
    overall_priority TEXT,
    segments_analyzed INTEGER DEFAULT 0,
    warnings_count INTEGER DEFAULT 0,
    config_json TEXT,
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_quality_runs_job ON quality_analysis_runs(job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_quality_runs_status ON quality_analysis_runs(status);

CREATE TABLE IF NOT EXISTS quality_warnings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT NOT NULL,
    explanation TEXT NOT NULL,
    evidence_json TEXT,
    segment_ids_json TEXT,
    start_sec REAL,
    end_sec REAL,
    review_status TEXT NOT NULL DEFAULT 'open',
    reviewer_note TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES quality_analysis_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_quality_warnings_job ON quality_warnings(job_id);
CREATE INDEX IF NOT EXISTS idx_quality_warnings_run ON quality_warnings(run_id);
CREATE INDEX IF NOT EXISTS idx_quality_warnings_status ON quality_warnings(review_status);

CREATE TABLE IF NOT EXISTS quality_reviews (
    id TEXT PRIMARY KEY,
    warning_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    action TEXT NOT NULL,
    note TEXT,
    actor TEXT NOT NULL DEFAULT 'session',
    created_at TEXT NOT NULL,
    FOREIGN KEY (warning_id) REFERENCES quality_warnings(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_quality_reviews_warning ON quality_reviews(warning_id);

CREATE TABLE IF NOT EXISTS quality_reference_evaluations (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    run_id TEXT,
    compare_against TEXT NOT NULL,
    reference_text TEXT NOT NULL,
    hypothesis_text TEXT NOT NULL,
    wer REAL,
    cer REAL,
    substitutions INTEGER,
    deletions INTEGER,
    insertions INTEGER,
    reference_words INTEGER,
    hypothesis_words INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_quality_eval_job ON quality_reference_evaluations(job_id);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    hostname TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    current_job_id TEXT,
    last_seen_at TEXT NOT NULL,
    meta_json TEXT
);

CREATE TABLE IF NOT EXISTS processing_metrics (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    model_name TEXT,
    backend TEXT,
    audio_duration_sec REAL,
    queue_wait_sec REAL,
    model_load_sec REAL,
    inference_sec REAL,
    finalize_sec REAL,
    total_sec REAL,
    peak_memory_bytes INTEGER,
    realtime_factor REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_processing_metrics_job ON processing_metrics(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority DESC, created_at ASC);

"""