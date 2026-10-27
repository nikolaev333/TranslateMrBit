# MrBit Transcription Studio

Audio-synchronized transcript editor for **TranslateMrBit**.

Original application feature. Whisper remains the speech recognition engine; Studio edits and exports are TranslateMrBit code.

## Features

- Timestamped segments from real Whisper `segments` (`start` / `end` / `text`)
- Authenticated audio streaming with HTTP Range seeking
- Click-to-seek, active-segment highlight, optional auto-scroll
- Inline edit, undo/redo, restore original, split (real playhead time), merge adjacent
- Autosave with optimistic concurrency (`transcript_revision`)
- Revision history
- Export TXT / SRT / VTT / JSON as **original** or **edited**
- Soft Smart Vocabulary suggestions when that module is available

Legacy jobs without timed Whisper JSON → `segments_status=unsupported` (no invented timings).

## Database

Backward-compatible migrations via `Database._ensure_columns` + `SCHEMA_SQL`:

| Table / column | Purpose |
|----------------|---------|
| `jobs.segments_status` | `pending` \| `ready` \| `unsupported` |
| `jobs.transcript_revision` | Optimistic concurrency |
| `jobs.has_word_timestamps` | True only if Whisper provided word timings |
| `transcript_segments` | Persistent segments |
| `transcript_revisions` | Edit history |

## API (auth required)

| Method | Path |
|--------|------|
| GET | `/api/jobs/{id}/segments` |
| PATCH | `/api/jobs/{id}/segments/{segment_id}` |
| POST | `/api/jobs/{id}/segments/{segment_id}/restore` |
| POST | `/api/jobs/{id}/segments/split` |
| POST | `/api/jobs/{id}/segments/merge` |
| POST | `/api/jobs/{id}/segments/save` |
| GET | `/api/jobs/{id}/revisions` |
| GET | `/api/jobs/{id}/audio` |
| GET | `/api/jobs/{id}/export?format=&source=` |

UI: `/studio/{job_id}`

## Local testing

```bash
.\.venv\Scripts\activate
pytest tests/test_transcription_studio.py tests/ -q
```

Manual: complete a transcription → History **Studio** or Transcribe **Open in Transcription Studio**.

## Coolify deploy (manual approval only)

1. Deploy `feature/transcription-studio` after review — do not auto-merge to `main`.
2. Keep existing volume mounts (`/data`, `/models`).
3. Confirm `/health` and worker healthcheck exclusion.
4. New tables/columns apply on API/worker startup against the existing SQLite file.
