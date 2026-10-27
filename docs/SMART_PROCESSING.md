# MrBit Smart Processing Engine

Original orchestration, resource analysis, scheduling, recovery, and monitoring for TranslateMrBit.

OpenAI Whisper remains a third-party speech recognition model. This document describes TranslateMrBit application infrastructure only.

## Implemented capabilities

- Lightweight resource monitoring (`psutil` + optional cgroup limits; no torch in API)
- Model profiles and hardware-aware recommendations (never silent model substitution)
- Observed peak memory from completed jobs feeds recommendations when available
- Inference backends: `openai-whisper` (default) and optional `faster-whisper`
- Extended job states with validated transitions, priorities, atomic SQLite claims, cancel/retry
- Worker leases + optional job timeouts + stale-lease recovery
- Resource-aware admission with configurable safety margins
- Optional long-audio chunking (fixed or silence-aware; feature-flagged; whole-file fallback)
- Worker heartbeats, interrupted-job recovery, processing metrics / RTF
- Job submission rate limiting
- Dashboard at `/processing` and authenticated processing APIs
- `JobQueueAdapter` port with SQLite implementation; Postgres/Redis adapters planned only

## Not claimed / future work

- Multi-host distributed queue (current design is single-server SQLite with atomic claims)
- Automatic production Coolify deploy or merge to `main` without explicit approval
- Guaranteed memory safety from estimates alone
- Quality improvement solely from chunking
- PostgreSQL / Redis queue adapters (interface documented; not enabled)

## Configuration (env)

| Variable | Default | Meaning |
|----------|---------|---------|
| `SMART_PROCESSING_ENABLED` | `true` | Enable admission / validation hooks |
| `PROCESSING_SAFETY_MARGIN_GB` | `0.75` | Extra free RAM required above estimate |
| `PROCESSING_ENFORCE_MEMORY_GATE` | `true` | Hold jobs when RAM estimate is insufficient |
| `PROCESSING_ALLOW_UNSAFE_MODELS` | `false` | Allow explicitly confirmed unsafe models |
| `PROCESSING_ENABLE_CHUNKING` | `false` | Optional long-audio chunking |
| `PROCESSING_CHUNK_SECONDS` | `600` | Chunk window when enabled |
| `PROCESSING_CHUNK_OVERLAP_SECONDS` | `1` | Overlap for boundary handling |
| `PROCESSING_MAX_RETRIES` | `2` | Automatic retry budget |
| `PROCESSING_JOB_TIMEOUT_SECONDS` | `0` | Wall-clock timeout (0 = off) |
| `PROCESSING_LEASE_SECONDS` | `1800` | Worker ownership lease |
| `JOB_SUBMIT_RATE_LIMIT` | `30` | Max job creates per window |
| `INFERENCE_BACKEND` | `openai-whisper` | Default backend id |
| `MAX_CONCURRENT_JOBS` | `1` | Worker concurrency |

### Multi-host (planned)

Requires shared uploads/outputs/models storage, a single logical DB or queue, and lease fencing across workers. See `app/services/smart_processing/queue_adapters.py`. Do not claim multi-host readiness until that stack is implemented and tested.

## Inference backends

### openai-whisper

Default. Uses existing worker `WhisperEngine`. Compatible with current Docker images.

### faster-whisper (optional)

```bash
pip install faster-whisper
```

Supports CPU INT8 and CUDA when correctly configured. Not assumed to always use less memory or run faster. Model identifiers are normalized at the application layer; do not confuse checkpoint formats across backends.

## API (authenticated)

- `GET /api/system/resources`
- `GET /api/system/workers`
- `GET /api/processing/queue`
- `GET /api/processing/jobs/{job_id}`
- `POST /api/processing/jobs/{job_id}/cancel`
- `POST /api/processing/jobs/{job_id}/retry`
- `GET /api/models`
- `GET /api/models/recommendations`
- `POST /api/models/download`
- `POST /api/models/validate?model=…&allow_unsafe=…`
- `GET /api/processing/metrics`

Job create (`POST /api/jobs`) accepts optional `inference_backend`, `priority`, `allow_unsafe_model`.

## Scheduling

1. Atomic claim of highest `priority`, then oldest `created_at` among `queued` / `waiting_for_resources`.
2. Concurrency gate via count of `preparing` / `processing` / `finalizing`.
3. Memory admission using approximate profiles + safety margin (or wait / reject).
4. Cancellation checked before and after inference.
5. Interrupted `processing` jobs fail on restart (avoid OOM death-loops); operators retry explicitly.

## Local development

```bash
pip install -r requirements.txt
# optional: pip install faster-whisper
pytest -q
```

API process must not import torch at startup. Run Whisper in the worker container/process.

## Safe Coolify procedure (manual)

1. Deploy from `feature/smart-processing` only after review (do not auto-merge `main`).
2. Preserve existing volumes for `/data` and `/models`.
3. Do not delete the SQLite database or uploaded files.
4. Keep `RUN_EMBEDDED_WORKER=false` in production Compose (separate worker).
5. Start with `PROCESSING_ENFORCE_MEMORY_GATE=true` and a model that fits host RAM.
6. Confirm `/health` stays lightweight after rollout.

## Privacy

- Synthetic fixtures only in tests
- No transcript content in application logs for this feature
- No external AI API calls for transcription
- Run secret scanning before any push
