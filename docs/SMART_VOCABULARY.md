# MrBit Smart Vocabulary

Application-level terminology recognition and correction for **TranslateMrBit**.

This is original TranslateMrBit software. It does **not** modify or claim ownership of the OpenAI Whisper neural network. Whisper remains an MIT-licensed dependency used for speech-to-text only.

## Pipeline

```
Audio upload
  → Worker loads enabled vocabulary terms
  → Optional Whisper initial_prompt (short terminology context)
  → Whisper transcription (original text stored permanently)
  → Smart Vocabulary analysis (matching + suggestions)
  → User review (accept / reject / edit)
  → reviewed_text saved separately
  → Optional WER vs human reference
```

Unsafe automatic replacement of the original transcript is intentionally not performed.

## Database migrations (backward compatible)

On connect, `Database._ensure_columns()` adds missing columns to existing production SQLite databases:

| Column | Purpose |
|--------|---------|
| `reviewed_text` | User-approved transcription (original untouched) |
| `vocabulary_analysis_json` | Cached match suggestions |
| `vocabulary_prompt_used` | Whisper `initial_prompt` that was sent |
| `reference_transcript` | Optional human reference for WER |
| `suggestions_accepted` | Accept/edit count for last save |
| `suggestions_rejected` | Reject count for last save |

New table `vocabulary_entries`:

- `id`, `correct_term`, `alternatives` (JSON array text)
- `language`, `category`, `description`
- `enabled`, `created_at`, `updated_at`

Existing volumes, job rows, and output paths are preserved.

## Matching engine (original application code)

Module: `app/services/smart_vocabulary.py`

Algorithms implemented in this project (not Whisper):

1. Unicode NFC normalization
2. Case-insensitive, word-boundary-aware phrase matching (Latin + Cyrillic)
3. Multi-word terminology detection
4. Longest-first / non-overlapping match resolution
5. Suggestion generation without mutating the original string in storage
6. Per-match accept / reject / edit → `reviewed_text`
7. Bounded `initial_prompt` builder for Whisper context

WER helper: `app/services/wer.py` (classic word-level Levenshtein). Measured values only.

## API (authenticated)

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/vocabulary` | List / create |
| GET/PUT/DELETE | `/api/vocabulary/{id}` | Read / update / delete |
| POST/GET | `/api/jobs/{id}/analyze` | Run / fetch analysis |
| POST | `/api/jobs/{id}/corrections` | Save reviewed text |
| GET | `/api/jobs/{id}/download-reviewed` | Download reviewed TXT |
| POST | `/api/jobs/{id}/evaluate` | Optional WER |

UI: `/vocabulary` (CRUD) and Smart Corrections on the transcribe result page.

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `INFERENCE_BACKEND` | `openai-whisper` | Reserved for future `faster-whisper` |
| `VOCABULARY_PROMPT_MAX_CHARS` | `800` | Max Whisper prompt size |
| `VOCABULARY_AUTO_ANALYZE` | `true` | Analyze after each completed job |
| `VOCABULARY_SEED_DEFAULTS` | `true` | Seed demo iGaming terms if empty |
| `WHISPER_MODEL` / `WHISPER_DEVICE` | unchanged | Model & CPU/GPU selection |
| `MAX_CONCURRENT_JOBS` | `1` | Concurrent worker capacity |

## Local testing

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
```

Smart Vocabulary focused:

```bash
pytest tests/test_smart_vocabulary.py -q
```

Manual UI:

1. Set `APP_PASSWORD`, `SESSION_SECRET`, `SESSION_HTTPS_ONLY=false` for local HTTP.
2. Run API with `RUN_EMBEDDED_WORKER=true` (dev) or separate worker process.
3. Open `/vocabulary`, add terms, transcribe audio, review Smart Corrections.

## Coolify / production deploy (manual approval required)

Do **not** deploy automatically. When approved:

1. Merge `feature/smart-vocabulary` after review (or deploy the branch explicitly).
2. Keep existing Docker volume names and mount paths (`/data`, `/models`).
3. Redeploy the Coolify resource for this Compose stack so API + worker pick up the image.
4. Confirm `/health` still returns 200; worker healthcheck remains `NONE` / excluded.
5. Confirm SQLite migrations applied (new columns / `vocabulary_entries` table).
6. Demo may keep `WHISPER_MODEL=small` on the current 3.7 GB VPS; production can raise model/device via env without code changes.

Whisper may still miss terms despite `initial_prompt` — Smart Corrections are the reliable layer.
