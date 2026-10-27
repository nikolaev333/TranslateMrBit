"""Pydantic response / request schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    version: str
    device: str
    model_loaded: str | None = None


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class MessageResponse(BaseModel):
    message: str


class JobCreateResponse(BaseModel):
    id: str
    status: str
    original_filename: str
    model_name: str
    language: str
    message: str = "Job queued"


class JobResponse(BaseModel):
    id: str
    original_filename: str
    file_size: int
    model_name: str
    language: str
    task: str
    status: str
    progress_message: str | None = None
    error_message: str | None = None
    detected_language: str | None = None
    duration_seconds: float | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str
    has_result: bool = False


class JobResultResponse(BaseModel):
    id: str
    status: str
    transcription_text: str | None = None
    detected_language: str | None = None
    duration_seconds: float | None = None
    available_formats: list[str] = Field(default_factory=list)


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int


class ErrorResponse(BaseModel):
    detail: str


class OptionsResponse(BaseModel):
    models: list[str]
    languages: list[str]
    default_model: str
    default_language: str
    max_upload_mb: int
    allowed_extensions: list[str]


DownloadFormat = Literal["txt", "srt", "vtt", "json"]


def job_row_to_response(row: dict[str, Any]) -> JobResponse:
    return JobResponse(
        id=row["id"],
        original_filename=row["original_filename"],
        file_size=row["file_size"],
        model_name=row["model_name"],
        language=row["language"],
        task=row["task"],
        status=row["status"],
        progress_message=row.get("progress_message"),
        error_message=row.get("error_message"),
        detected_language=row.get("detected_language"),
        duration_seconds=row.get("duration_seconds"),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        updated_at=row["updated_at"],
        has_result=bool(row.get("transcription_text")),
    )
