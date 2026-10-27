"""MrBit Smart Vocabulary CRUD API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth import require_auth
from app.database import Database
from app.schemas import (
    MessageResponse,
    VocabularyEntryCreate,
    VocabularyEntryResponse,
    VocabularyEntryUpdate,
    VocabularyListResponse,
)

router = APIRouter(
    prefix="/api/vocabulary",
    tags=["vocabulary"],
    dependencies=[Depends(require_auth)],
)


def get_db(request: Request) -> Database:
    return request.app.state.db


def _to_response(row: dict) -> VocabularyEntryResponse:
    return VocabularyEntryResponse(
        id=row["id"],
        correct_term=row["correct_term"],
        alternatives=list(row.get("alternatives") or []),
        language=row.get("language") or "any",
        category=row.get("category") or "General",
        description=row.get("description") or "",
        enabled=bool(row.get("enabled", True)),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=VocabularyListResponse)
async def list_vocabulary(
    db: Annotated[Database, Depends(get_db)],
    language: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    entries = await db.list_vocabulary_entries(
        language=language,
        category=category,
        enabled=enabled,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await db.count_vocabulary_entries(
        language=language,
        category=category,
        enabled=enabled,
        search=search,
    )
    categories = await db.list_vocabulary_categories()
    return VocabularyListResponse(
        entries=[_to_response(e) for e in entries],
        total=total,
        categories=categories,
    )


@router.post("", response_model=VocabularyEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_vocabulary(
    payload: VocabularyEntryCreate,
    db: Annotated[Database, Depends(get_db)],
):
    try:
        row = await db.create_vocabulary_entry(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_response(row)


@router.get("/{entry_id}", response_model=VocabularyEntryResponse)
async def get_vocabulary(
    entry_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    row = await db.get_vocabulary_entry(entry_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vocabulary entry not found")
    return _to_response(row)


@router.put("/{entry_id}", response_model=VocabularyEntryResponse)
async def update_vocabulary(
    entry_id: str,
    payload: VocabularyEntryUpdate,
    db: Annotated[Database, Depends(get_db)],
):
    existing = await db.get_vocabulary_entry(entry_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vocabulary entry not found")
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "correct_term" in data and not str(data["correct_term"]).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="correct_term cannot be empty",
        )
    row = await db.update_vocabulary_entry(entry_id, **data)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vocabulary entry not found")
    return _to_response(row)


@router.delete("/{entry_id}", response_model=MessageResponse)
async def delete_vocabulary(
    entry_id: str,
    db: Annotated[Database, Depends(get_db)],
):
    deleted = await db.delete_vocabulary_entry(entry_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vocabulary entry not found")
    return MessageResponse(message="Vocabulary entry deleted")
