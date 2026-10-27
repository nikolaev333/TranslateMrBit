# Whisper AI – Self-Hosted Web Transcription

Private, production-ready web UI for **OpenAI Whisper** (`openai-whisper`) on a single OVHcloud server via **Coolify + Docker**.

Preserves the same behaviour as a local CLI run:

```bash
python -m whisper audio.mp3 --language Bulgarian --task transcribe --output_format txt --model large
```

- Default model: `large` (never silently substituted)
- Default language: `Bulgarian`
- Task: `transcribe` (not translate)
- Outputs: TXT (plus SRT / VTT / JSON)
- Single-user password auth, SQLite job queue, persistent volumes for uploads / outputs / DB / model weights

---

## Architecture

```
Browser (HTTPS)
    │
    ▼
Coolify reverse proxy (TLS)
    │
    ▼
whisper (API)  Uvicorn + FastAPI  (:8000)
    ├── Auth (HTTP-only session cookie)
    ├── Upload + FFprobe validation
    ├── SQLite job store (/data/db)
    ├── /health (never loads torch)
    └── Enqueue model downloads for the worker
            │
            ▼  shared volumes (/data, /models)
whisper-worker  (separate process/container)
    ├── Job loop (1 at a time)
    ├── Model preload downloads
    └── openai-whisper + PyTorch (CPU by default)
            └── weights in /models
```

If the worker OOMs on `large`, the API container stays up and Coolify keeps
routing. Mark the job failed, then retry with `small` before `large`.

| Path | Purpose |
|------|---------|
| `/data/uploads` | Uploaded audio/video |
| `/data/outputs` | Generated TXT/SRT/VTT/JSON |
| `/data/db` | SQLite database |
| `/models` | Whisper model weights (persistent) |

---

## Requirements

- Docker Engine 24+ (and Docker Compose v2 for local testing)
- ~10 GB free disk for the `large` model + workspace
- 8 GB+ RAM recommended for `large` on CPU (16 GB+ preferred)
- Optional: NVIDIA GPU + NVIDIA Container Toolkit for CUDA

---

## Local Docker installation

```bash
git clone <your-repo-url> TranslateMrBit
cd TranslateMrBit
cp .env.example .env
```

Edit `.env`:

```env
APP_PASSWORD=choose-a-strong-password
SESSION_SECRET=generate-a-long-random-string
SESSION_HTTPS_ONLY=false
```

Generate a secret (example):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Build and start:

```bash
docker compose up -d --build
```

Open: [http://localhost:8000](http://localhost:8000)

Health check: [http://localhost:8000/health](http://localhost:8000/health)

Logs:

```bash
docker compose logs -f whisper
```

Stop:

```bash
docker compose down
```

Volumes (`whisper_uploads`, `whisper_outputs`, `whisper_db`, `whisper_models`) keep data across rebuilds.

---

## Local development (without full Docker)

Python 3.11+, FFmpeg on `PATH`.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements-torch-cpu.txt
pip install -r requirements.txt

# Use local paths
set DATA_DIR=./data
set UPLOAD_DIR=./data/uploads
set OUTPUT_DIR=./data/outputs
set DB_PATH=./data/db/whisper.db
set WHISPER_MODEL_DIR=./models
set APP_PASSWORD=dev-password
set SESSION_SECRET=dev-session-secret-change-me
set SESSION_HTTPS_ONLY=false

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

On PowerShell, use `$env:NAME="value"` instead of `set`.

---

## How to use

1. Open the site → login with `APP_PASSWORD`.
2. Drag & drop an MP3 (or WAV/M4A/MP4/FLAC/OGG/…).
3. Keep **Model = large**, **Language = Bulgarian**.
4. Click **Start transcription**.
5. Wait for status: Queued → Processing → Completed.
6. Copy text, or download **TXT** / SRT / VTT / JSON.
7. Browse older jobs under **History**.

### Example: Bulgarian MP3 → TXT (`large`)

Same intent as your Windows command:

| Setting | Value |
|---------|--------|
| File | `843819.mp3` (or any MP3) |
| Model | `large` |
| Language | `Bulgarian` |
| Task | `transcribe` (fixed by the app) |
| Output | TXT download |

The worker calls Whisper with `task="transcribe"`, `language="Bulgarian"`, `model="large"`, and writes the official Whisper TXT writer output under `/data/outputs/<job_id>/`.

---

## Deploy to OVHcloud with Coolify

### 1. Push to GitHub

Create a GitHub repository and push this project (do **not** commit `.env`).

### 2. Create application in Coolify

Prefer **Docker Compose** (this repo’s `docker-compose.yml`) so Coolify runs both:

- `whisper` — FastAPI/Uvicorn API on port **8000** (attach the public domain here)
- `whisper-worker` — separate transcription/download process (no public port)

If you deploy Dockerfile-only, the API stays up but jobs will not process until a worker runs (`python -m app.worker_main`) with the same volumes/env.

1. **New Resource** → **Docker Compose**.
2. Connect the GitHub repo; compose file `/docker-compose.yml`.
3. Map the public domain / proxy to the **`whisper`** service, port **8000**.
4. Set environment variables (see below).
5. Keep the named volumes (uploads / outputs / db / models).
6. Enable **HTTPS** / Let’s Encrypt when you have a real domain.
7. Deploy (Redeploy after pulls).

### Coolify environment variables

| Variable | Example | Notes |
|----------|---------|--------|
| `APP_ENV` | `production` | |
| `APP_PASSWORD` | *(strong secret)* | Login password |
| `SESSION_SECRET` | *(long random)* | Cookie signing |
| `SESSION_HTTPS_ONLY` | `true` | Required behind HTTPS |
| `WHISPER_MODEL` | `large` | Default model |
| `WHISPER_LANGUAGE` | `Bulgarian` | Default language |
| `WHISPER_DEVICE` | `cpu` | Use `cpu` on OVH without GPU; `cuda` only with NVIDIA toolkit |
| `RUN_EMBEDDED_WORKER` | `false` | Keep false when using the `whisper-worker` service |
| `WHISPER_MODEL_DIR` | `/models` | Weight cache |
| `DATA_DIR` | `/data` | |
| `UPLOAD_DIR` | `/data/uploads` | |
| `OUTPUT_DIR` | `/data/outputs` | |
| `DB_PATH` | `/data/db/whisper.db` | |
| `MAX_UPLOAD_MB` | `500` | |
| `MAX_CONCURRENT_JOBS` | `1` | Keep at 1 on small VPS |
| `AUTO_DELETE_UPLOADS_DAYS` | `7` | Deletes source audio only, not transcripts |

### Persistent volume paths (Coolify)

Map host/storage volumes to:

| Container path | Suggested Coolify volume name |
|----------------|-------------------------------|
| `/data/uploads` | `whisper-uploads` |
| `/data/outputs` | `whisper-outputs` |
| `/data/db` | `whisper-db` |
| `/models` | `whisper-models` |

If using the included `docker-compose.yml` in Coolify, the named volumes are already declared.

### Custom domain + HTTPS

1. Point DNS `A`/`AAAA` records for e.g. `whisper.example.com` to your OVHcloud server IP.
2. In Coolify → Domains → add `whisper.example.com`.
3. Enable Let’s Encrypt / Traefik TLS.
4. Ensure `SESSION_HTTPS_ONLY=true`.
5. Open `https://whisper.example.com` from any laptop/phone and log in.

No SSH or RDP is required for day-to-day transcription after deploy.

### Health check & restart

- Health endpoint: `GET /health` (never loads torch; `model_loaded: null` is normal for the API)
- Dockerfile `HEALTHCHECK` uses `curl` against `http://127.0.0.1:8000/health`
- Compose `restart: unless-stopped` on API and worker
- Worker OOM (exit 137) should not mark the API Degraded

### After redeploy — verify routing

```bash
# From your laptop (replace with your Coolify URL)
curl -fsS http://YOUR-HOST/health
# Expect: {"status":"ok","version":"1.0.0","device":"cpu","model_loaded":null}

curl -fsS -o /dev/null -w "%{http_code}\n" http://YOUR-HOST/login
# Expect: 200

# Login then enqueue a small-model download (optional)
# POST /api/login  {"password":"..."}
# POST /api/models/preload  {"models":["small"]}
# GET  /api/models/preload/status
```

In Coolify: API service **Running** + healthy; worker **Running**. Intermittent Traefik `404 page not found` usually means the API container was restarting or failing healthchecks — check Runtime Logs for `event=api_ready` and worker `event=worker_ready`. Test transcription with model **`small`** before **`large`** on low-RAM VPS hosts.

---

## CPU vs NVIDIA GPU

### Default (CPU)

The shipping `Dockerfile` installs **CPU** PyTorch wheels. Works on any OVHcloud VPS without a GPU. The `large` model is slow on CPU but correct.

### Optional NVIDIA GPU

1. Install NVIDIA drivers + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) on the host.
2. Use a CUDA base image / CUDA PyTorch wheels, for example:

```dockerfile
# Sketch only — adapt CUDA version to your host
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
# install ffmpeg, copy app, pip install -r requirements.txt (without CPU torch)
```

3. Run with GPU access:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```

4. Set `WHISPER_DEVICE=cuda` (or `auto`). FP16 is enabled automatically on CUDA.

Do **not** assume CUDA exists; the app falls back to CPU when unavailable.

---

## Upgrading without losing data

1. Keep the four persistent volumes mounted.
2. Pull / redeploy the new image in Coolify.
3. Model weights in `/models` and job history in `/data/db` remain.
4. Interrupted jobs left in `processing` are **requeued** on startup.

```bash
# Local compose example
docker compose pull   # if using a registry image
docker compose up -d --build
```

---

## API overview

All job endpoints require authentication (session cookie after `POST /api/login`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness (public) |
| POST | `/api/login` | Password login |
| POST | `/api/logout` | Clear session |
| POST | `/api/jobs` | Upload + enqueue |
| GET | `/api/jobs` | List jobs |
| GET | `/api/jobs/{id}` | Status |
| GET | `/api/jobs/{id}/result` | Transcription JSON |
| GET | `/api/jobs/{id}/download?format=txt` | Download txt/srt/vtt/json |
| DELETE | `/api/jobs/{id}` | Delete job + files |

Polling: the UI calls `GET /api/jobs/{id}` every ~2s (no WebSockets).

---

## Tests

Tests mock Whisper so they never download `large`.

```bash
pip install -r requirements-torch-cpu.txt
pip install -r requirements.txt
pytest -q
```

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| Container unhealthy | `docker logs <container>`; hit `/health`; ensure port 8000 |
| Login loop / cookies | `SESSION_HTTPS_ONLY` must match HTTP vs HTTPS; proxy must forward `X-Forwarded-Proto` |
| 413 / upload fails | Raise `MAX_UPLOAD_MB`; check reverse-proxy body size limits |
| “Invalid media file” | Confirm FFmpeg in image; file actually contains audio |
| OOM killed | Use smaller model (`medium`/`small`) or more RAM; keep `MAX_CONCURRENT_JOBS=1` |
| Model re-downloads every deploy | `/models` volume not mounted |
| History empty after redeploy | `/data/db` volume not mounted |
| Slow first job | First run downloads `large` (~3 GB) into `/models` |
| CUDA not used | Host toolkit, compose GPU reservation, `WHISPER_DEVICE=auto` |

### Useful commands on the server

```bash
docker ps
docker logs -f <container_name>
docker stats
df -h
free -h
curl -fsS http://127.0.0.1:8000/health
```

---

## Security notes

- Change `APP_PASSWORD` and `SESSION_SECRET` before exposing the app.
- Never commit `.env`.
- Uploads are stored under UUID paths; not served as static public files.
- Login is rate-limited.
- API errors do not echo secrets or stack traces to clients.

---

## License

This project wires together FastAPI and the open-source [openai-whisper](https://github.com/openai/whisper) package. Respect Whisper / PyTorch / dependency licenses for your deployment.
