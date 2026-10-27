# CPU-compatible production image for Whisper AI web app
# Optional GPU image notes: see README (NVIDIA / CUDA section)

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOME=/app \
    DATA_DIR=/data \
    UPLOAD_DIR=/data/uploads \
    OUTPUT_DIR=/data/outputs \
    DB_PATH=/data/db/whisper.db \
    WHISPER_MODEL_DIR=/models \
    HOME=/home/whisper

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin whisper \
    && mkdir -p /data/uploads /data/outputs /data/db /models \
    && chown -R whisper:whisper /data /models /home/whisper

WORKDIR /app

# Install CPU PyTorch first (smaller / no CUDA libs), then the rest
COPY requirements-torch-cpu.txt requirements.txt /app/
# openai-whisper still imports pkg_resources at build time; disable build isolation
# so the pinned setuptools (with pkg_resources) from the image is used.
RUN pip install --upgrade pip \
    && pip install "setuptools>=70,<81" wheel \
    && pip install -r /app/requirements-torch-cpu.txt \
    && pip install --no-build-isolation -r /app/requirements.txt

COPY --chown=whisper:whisper app /app/app

USER whisper

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
