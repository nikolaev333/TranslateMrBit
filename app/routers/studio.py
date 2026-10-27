"""MrBit Transcription Studio API — segments, audio stream, export, revisions."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.database import ConflictError, Database
from app.models import JobStatus, SegmentsStatus
from app.services.transcript_segments import (
    guess_content_type,
    join_segment_texts,
    normalize_whisper_segments,
    segments_to_json_export,
    segments_to_srt,
    segments_to_vtt,
)

router = APIRouter(
    prefix="/api/jobs",
    tags=["studio"],
    dependencies=[Depends(require_auth)],
)


def get_db(request: Request) -> Database:
    return request.app.state.db


# ---- Schemas -----------------------------------------------------------------


class SegmentResponse(BaseModel):
    id: str
    job_id: str
    position: int
    start: float
    end: float
    original_text: str
    edited_text: str
    is_edited: bool
    edit_source: str | None = None
    has_words: bool = False


class SegmentsListResponse(BaseModel):
    job_id: str
    segments_status: str
    transcript_revision: int
    has_word_timestamps: bool = False
    duration_seconds: float | None = None
    audio_available: bool = False
    segments: list[SegmentResponse] = Field(default_factory=list)


class SegmentPatchRequest(BaseModel):
    edited_text: str = Field(max_length=100_000)
    expected_revision: int | None = None
    edit_source: Literal["manual", "vocabulary"] | None = "manual"


class SegmentSaveItem(BaseModel):
    id: str
    edited_text: str = Field(max_length=100_000)
    edit_source: Literal["manual", "vocabulary"] | None = "manual"


class BulkSaveRequest(BaseModel):
    expected_revision: int
    changes: list[SegmentSaveItem] = Field(default_factory=list)


class SplitRequest(BaseModel):
    segment_id: str
    split_at: float
    left_text: str | None = None
    right_text: str | None = None
    expected_revision: int | None = None


class MergeRequest(BaseModel):
    left_id: str
    right_id: str
    expected_revision: int | None = None


class RevisionResponse(BaseModel):
    id: str
    job_id: str
    segment_id: str | None = None
    revision_no: int
    previous_text: str | None = None
    new_text: str | None = None
    action: str
    actor: str
    created_at: str


class RevisionsListResponse(BaseModel):
    job_id: str
    revisions: list[RevisionResponse]
    transcript_revision: int


class MutationResponse(BaseModel):
    job_id: str
    transcript_revision: int
    segments: list[SegmentResponse]
    segment: SegmentResponse | None = None


def _seg_response(row: dict[str, Any]) -> SegmentResponse:
    return SegmentResponse(
        id=row["id"],
        job_id=row["job_id"],
        position=int(row["position"]),
        start=float(row["start_sec"]),
        end=float(row["end_sec"]),
        original_text=row.get("original_text") or "",
        edited_text=row.get("edited_text") or "",
        is_edited=bool(row.get("is_edited")),
        edit_source=row.get("edit_source"),
        has_words=bool(row.get("words_json")),
    )


async def _require_job(db: Database, job_id: str) -> dict[str, Any]:
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


async def _try_import_legacy_segments(db: Database, job: dict[str, Any]) -> str:
    """Import timed segments from Whisper JSON once; never invent timings."""
    job_id = job["id"]
    existing = await db.count_segments(job_id)
    if existing > 0:
        await db.update_job(job_id, segments_status=SegmentsStatus.READY.value)
        return SegmentsStatus.READY.value

    json_path = job.get("output_json_path") or ""
    if not json_path or not Path(json_path).is_file():
        await db.update_job(job_id, segments_status=SegmentsStatus.UNSUPPORTED.value)
        return SegmentsStatus.UNSUPPORTED.value

    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        await db.update_job(job_id, segments_status=SegmentsStatus.UNSUPPORTED.value)
        return SegmentsStatus.UNSUPPORTED.value

    segments, has_words = normalize_whisper_segments(data if isinstance(data, dict) else {})
    if not segments:
        await db.update_job(job_id, segments_status=SegmentsStatus.UNSUPPORTED.value)
        return SegmentsStatus.UNSUPPORTED.value

    await db.replace_job_segments(
        job_id,
        segments,
        has_word_timestamps=has_words,
        status=SegmentsStatus.READY.value,
    )
    return SegmentsStatus.READY.value


def _audio_available(job: dict[str, Any]) -> bool:
    path = job.get("upload_path") or ""
    return bool(path) and Path(path).is_file()


# ---- Endpoints ---------------------------------------------------------------


@router.get("/{job_id}/segments", response_model=SegmentsListResponse)
async def get_segments(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    job = await _require_job(db, job_id)
    seg_status = (job.get("segments_status") or SegmentsStatus.PENDING.value).lower()

    if seg_status != SegmentsStatus.READY.value:
        count = await db.count_segments(job_id)
        if count == 0 and job.get("status") == JobStatus.COMPLETED.value:
            seg_status = await _try_import_legacy_segments(db, job)
            job = await db.get_job(job_id) or job
        elif count > 0:
            seg_status = SegmentsStatus.READY.value
            await db.update_job(job_id, segments_status=seg_status)

    segments = await db.list_segments(job_id) if seg_status == SegmentsStatus.READY.value else []
    return SegmentsListResponse(
        job_id=job_id,
        segments_status=seg_status,
        transcript_revision=int(job.get("transcript_revision") or 0),
        has_word_timestamps=bool(job.get("has_word_timestamps")),
        duration_seconds=job.get("duration_seconds"),
        audio_available=_audio_available(job),
        segments=[_seg_response(s) for s in segments],
    )


@router.patch("/{job_id}/segments/{segment_id}", response_model=MutationResponse)
async def patch_segment(
    job_id: str,
    segment_id: str,
    payload: SegmentPatchRequest,
    db: Annotated[Database, Depends(get_db)],
):
    await _require_job(db, job_id)
    try:
        seg, rev = await db.update_segment_text(
            job_id,
            segment_id,
            payload.edited_text,
            expected_revision=payload.expected_revision,
            edit_source=payload.edit_source,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    segments = await db.list_segments(job_id)
    return MutationResponse(
        job_id=job_id,
        transcript_revision=rev,
        segments=[_seg_response(s) for s in segments],
        segment=_seg_response(seg),
    )


@router.post("/{job_id}/segments/{segment_id}/restore", response_model=MutationResponse)
async def restore_segment(
    job_id: str,
    segment_id: str,
    db: Annotated[Database, Depends(get_db)],
    expected_revision: Annotated[int | None, Query()] = None,
):
    await _require_job(db, job_id)
    try:
        seg, rev = await db.restore_segment(
            job_id, segment_id, expected_revision=expected_revision
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    segments = await db.list_segments(job_id)
    return MutationResponse(
        job_id=job_id,
        transcript_revision=rev,
        segments=[_seg_response(s) for s in segments],
        segment=_seg_response(seg),
    )


@router.post("/{job_id}/segments/split", response_model=MutationResponse)
async def split_segment(
    job_id: str,
    payload: SplitRequest,
    db: Annotated[Database, Depends(get_db)],
):
    await _require_job(db, job_id)
    try:
        segments, rev = await db.split_segment(
            job_id,
            payload.segment_id,
            payload.split_at,
            left_text=payload.left_text,
            right_text=payload.right_text,
            expected_revision=payload.expected_revision,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MutationResponse(
        job_id=job_id,
        transcript_revision=rev,
        segments=[_seg_response(s) for s in segments],
    )


@router.post("/{job_id}/segments/merge", response_model=MutationResponse)
async def merge_segments(
    job_id: str,
    payload: MergeRequest,
    db: Annotated[Database, Depends(get_db)],
):
    await _require_job(db, job_id)
    try:
        segments, rev = await db.merge_segments(
            job_id,
            payload.left_id,
            payload.right_id,
            expected_revision=payload.expected_revision,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MutationResponse(
        job_id=job_id,
        transcript_revision=rev,
        segments=[_seg_response(s) for s in segments],
    )


@router.post("/{job_id}/segments/save", response_model=MutationResponse)
async def bulk_save_segments(
    job_id: str,
    payload: BulkSaveRequest,
    db: Annotated[Database, Depends(get_db)],
):
    await _require_job(db, job_id)
    try:
        segments, rev = await db.bulk_save_segments(
            job_id,
            [c.model_dump() for c in payload.changes],
            expected_revision=payload.expected_revision,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return MutationResponse(
        job_id=job_id,
        transcript_revision=rev,
        segments=[_seg_response(s) for s in segments],
    )


@router.get("/{job_id}/revisions", response_model=RevisionsListResponse)
async def get_revisions(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    job = await _require_job(db, job_id)
    rows = await db.list_revisions(job_id, limit=limit, offset=offset)
    return RevisionsListResponse(
        job_id=job_id,
        transcript_revision=int(job.get("transcript_revision") or 0),
        revisions=[
            RevisionResponse(
                id=r["id"],
                job_id=r["job_id"],
                segment_id=r.get("segment_id"),
                revision_no=int(r["revision_no"]),
                previous_text=r.get("previous_text"),
                new_text=r.get("new_text"),
                action=r["action"],
                actor=r.get("actor") or "session",
                created_at=r["created_at"],
            )
            for r in rows
        ],
    )


@router.get("/{job_id}/audio")
async def stream_audio(
    job_id: str,
    request: Request,
    db: Annotated[Database, Depends(get_db)],
    range_header: Annotated[str | None, Header(alias="Range")] = None,
):
    """Authenticated audio streaming with HTTP Range support for seeking."""
    job = await _require_job(db, job_id)
    path_str = job.get("upload_path") or ""
    path = Path(path_str)
    if not path_str or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio file is not available for this job",
        )

    file_size = path.stat().st_size
    content_type = job.get("content_type") or guess_content_type(path.name)
    if not content_type or content_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(path.name)
        content_type = guessed or content_type or "application/octet-stream"

    filename = Path(job.get("original_filename") or path.name).name

    def _file_iterator(start: int, end: int, chunk: int = 64 * 1024):
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = fh.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    if range_header:
        # bytes=START-END
        unit, _, rng = range_header.partition("=")
        if unit.strip() != "bytes" or not rng:
            raise HTTPException(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                detail="Invalid Range header",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        start_s, _, end_s = rng.strip().partition("-")
        try:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else file_size - 1
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                detail="Invalid Range values",
                headers={"Content-Range": f"bytes */{file_size}"},
            ) from exc
        if start < 0 or end >= file_size or start > end:
            raise HTTPException(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                detail="Range not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        length = end - start + 1
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, no-store",
        }
        return StreamingResponse(
            _file_iterator(start, end),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=content_type,
            headers=headers,
        )

    # Full file — still avoid leaking filesystem paths
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Disposition": f'inline; filename="{filename}"',
        "Cache-Control": "private, no-store",
    }
    return FileResponse(
        path,
        media_type=content_type,
        filename=filename,
        headers=headers,
        content_disposition_type="inline",
    )


@router.get("/{job_id}/export")
async def export_transcript(
    job_id: str,
    db: Annotated[Database, Depends(get_db)],
    format: Annotated[Literal["txt", "srt", "vtt", "json"], Query()] = "txt",
    source: Annotated[Literal["original", "edited"], Query()] = "edited",
):
    job = await _require_job(db, job_id)
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transcription is not completed yet",
        )

    segments = await db.list_segments(job_id)
    use_edited = source == "edited"
    stem = Path(job["original_filename"]).stem
    suffix = "edited" if use_edited else "original"

    if segments:
        if format == "txt":
            body = join_segment_texts(segments, use_edited=use_edited) + "\n"
            media = "text/plain; charset=utf-8"
        elif format == "srt":
            body = segments_to_srt(segments, use_edited=use_edited)
            media = "application/x-subrip; charset=utf-8"
        elif format == "vtt":
            body = segments_to_vtt(segments, use_edited=use_edited)
            media = "text/vtt; charset=utf-8"
        else:
            body = segments_to_json_export(job_id, segments, use_edited=use_edited)
            media = "application/json; charset=utf-8"
        return Response(
            content=body.encode("utf-8"),
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="{stem}.{suffix}.{format}"'
            },
        )

    # Legacy fallback — never invent SRT/VTT timings
    if format == "txt":
        text = (
            job.get("reviewed_text")
            if use_edited and job.get("reviewed_text") is not None
            else job.get("transcription_text")
        ) or ""
        return Response(
            content=(text + ("\n" if text and not text.endswith("\n") else "")).encode(
                "utf-8"
            ),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{stem}.{suffix}.txt"'
            },
        )

    key_map = {
        "srt": "output_srt_path",
        "vtt": "output_vtt_path",
        "json": "output_json_path",
    }
    if use_edited:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Timed export of edited transcript requires Studio segments",
        )
    path_str = job.get(key_map[format]) or ""
    if not path_str or not Path(path_str).is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Format '{format}' is not available for this job",
        )
    media_types = {
        "srt": "application/x-subrip; charset=utf-8",
        "vtt": "text/vtt; charset=utf-8",
        "json": "application/json; charset=utf-8",
    }
    return FileResponse(
        path_str,
        media_type=media_types[format],
        filename=f"{stem}.{format}",
    )
