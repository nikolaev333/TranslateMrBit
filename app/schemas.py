"""Pydantic response / request schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Literal is used by DownloadFormat, Corrections, Evaluate schemas.


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
    progress_percent: float | None = None
    cpu_percent: float | None = None
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
    reviewed_text: str | None = None
    detected_language: str | None = None
    duration_seconds: float | None = None
    available_formats: list[str] = Field(default_factory=list)
    has_vocabulary_analysis: bool = False
    suggestions_accepted: int = 0
    suggestions_rejected: int = 0


class VocabularyEntryCreate(BaseModel):
    correct_term: str = Field(min_length=1, max_length=256)
    alternatives: list[str] = Field(default_factory=list)
    language: str = Field(default="any", max_length=64)
    category: str = Field(default="General", max_length=64)
    description: str = Field(default="", max_length=1024)
    enabled: bool = True


class VocabularyEntryUpdate(BaseModel):
    correct_term: str | None = Field(default=None, min_length=1, max_length=256)
    alternatives: list[str] | None = None
    language: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1024)
    enabled: bool | None = None


class VocabularyEntryResponse(BaseModel):
    id: str
    correct_term: str
    alternatives: list[str] = Field(default_factory=list)
    language: str
    category: str
    description: str = ""
    enabled: bool = True
    created_at: str
    updated_at: str


class VocabularyListResponse(BaseModel):
    entries: list[VocabularyEntryResponse]
    total: int
    categories: list[str] = Field(default_factory=list)


class VocabularyMatchResponse(BaseModel):
    entry_id: str
    correct: str
    matched_text: str
    start: int
    end: int
    category: str
    language: str


class VocabularyAnalysisResponse(BaseModel):
    job_id: str
    original_text: str
    suggested_text: str
    matches: list[VocabularyMatchResponse] = Field(default_factory=list)
    reviewed_text: str | None = None
    vocabulary_prompt_used: str | None = None


class CorrectionDecision(BaseModel):
    match_index: int
    action: Literal["accept", "reject", "edit"] = "reject"
    replacement: str | None = None


class CorrectionsRequest(BaseModel):
    decisions: list[CorrectionDecision] = Field(default_factory=list)
    reviewed_text: str | None = None
    restore_original: bool = False


class CorrectionsResponse(BaseModel):
    job_id: str
    original_text: str
    reviewed_text: str
    suggestions_accepted: int
    suggestions_rejected: int


class EvaluateRequest(BaseModel):
    reference_transcript: str = Field(min_length=1)
    compare_against: Literal["original", "reviewed"] = "reviewed"


class EvaluateResponse(BaseModel):
    job_id: str
    compare_against: str
    hypothesis_text: str
    reference_transcript: str
    wer: float
    reference_words: int
    hypothesis_words: int
    substitutions: int
    deletions: int
    insertions: int
    edits: int
    suggestions_accepted: int
    suggestions_rejected: int


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


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        progress_percent=_optional_float(row.get("progress_percent")),
        cpu_percent=_optional_float(row.get("cpu_percent")),
        error_message=row.get("error_message"),
        detected_language=row.get("detected_language"),
        duration_seconds=row.get("duration_seconds"),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        updated_at=row["updated_at"],
        has_result=bool(row.get("transcription_text")),
    )
