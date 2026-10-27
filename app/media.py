"""Upload validation and FFmpeg/FFprobe helpers."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.config import Settings

logger = logging.getLogger(__name__)

UNSAFE_NAME_RE = re.compile(r"[^\w.\-()+ ]+", re.UNICODE)


def sanitize_filename(filename: str | None) -> str:
    if not filename:
        return "upload.bin"
    name = Path(filename).name
    name = name.replace("\\", "_").replace("/", "_")
    name = UNSAFE_NAME_RE.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        return "upload.bin"
    return name[:200]


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


async def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FFmpeg/FFprobe is not available on the server",
        )


async def probe_media(path: Path) -> dict:
    """Validate that the file is a readable audio/video media file."""
    await ensure_ffmpeg_available()
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration:stream=codec_type,codec_name",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace")[:500]
        logger.warning("ffprobe failed for %s: %s", path, message)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unsupported media file",
        )

    import json

    try:
        data = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not parse media metadata",
        ) from exc

    streams = data.get("streams") or []
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not has_audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File does not contain an audio stream",
        )
    return data


async def save_upload(
    file: UploadFile, settings: Settings
) -> tuple[str, str, Path, int, str]:
    """Persist an upload under a UUID path and validate it.

    Returns: (job_id, original_filename, dest_path, size, content_type)
    """
    original = sanitize_filename(file.filename)
    ext = extension_of(original)
    if not ext or ext not in settings.allowed_extension_set:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File extension '.{ext or '?'}' is not allowed",
        )

    job_id = str(uuid.uuid4())
    stored_name = f"{job_id}.{ext}"
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / stored_name
    content_type = file.content_type or "application/octet-stream"

    size = 0
    chunk_size = 1024 * 1024
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds maximum size of {settings.max_upload_mb} MB",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        dest.unlink(missing_ok=True)
        if getattr(exc, "errno", None) == 28:
            raise HTTPException(
                status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
                detail="Insufficient disk space to store upload",
            ) from exc
        logger.exception("Failed to save upload")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store uploaded file",
        ) from exc
    finally:
        await file.close()

    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    try:
        await probe_media(dest)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    return job_id, original, dest, size, content_type


def safe_unlink(path: str | Path | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete file: %s", path)


def delete_job_files(job: dict) -> None:
    for key in (
        "upload_path",
        "output_txt_path",
        "output_srt_path",
        "output_vtt_path",
        "output_json_path",
    ):
        safe_unlink(job.get(key))
