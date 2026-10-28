"""One-shot prod SQLite column migration for Coolify volume."""
from __future__ import annotations

import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/data/db/whisper.db"

conn = sqlite3.connect(DB)
cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
print("before_priority", "priority" in cols)
migrations = [
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
    if name not in cols:
        print("adding", name)
        conn.execute(sql)
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority DESC, created_at ASC)"
)
conn.commit()
cols2 = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
print("after_priority", "priority" in cols2)
conn.close()
print("ok")
