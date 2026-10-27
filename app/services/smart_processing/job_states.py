"""Legal job status transitions for MrBit Smart Processing."""

from __future__ import annotations

from app.models import JobStatus

# Deterministic allowed edges. Unknown transitions are rejected.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    JobStatus.QUEUED.value: frozenset(
        {
            JobStatus.WAITING_FOR_RESOURCES.value,
            JobStatus.PREPARING.value,
            JobStatus.CANCELLED.value,
            JobStatus.FAILED.value,
        }
    ),
    JobStatus.WAITING_FOR_RESOURCES.value: frozenset(
        {
            JobStatus.QUEUED.value,
            JobStatus.PREPARING.value,
            JobStatus.CANCELLED.value,
            JobStatus.FAILED.value,
        }
    ),
    JobStatus.PREPARING.value: frozenset(
        {
            JobStatus.WAITING_FOR_RESOURCES.value,
            JobStatus.PROCESSING.value,
            JobStatus.QUEUED.value,  # expired lease / release
            JobStatus.CANCELLED.value,
            JobStatus.FAILED.value,
        }
    ),
    JobStatus.PROCESSING.value: frozenset(
        {
            JobStatus.FINALIZING.value,
            JobStatus.CANCELLED.value,
            JobStatus.FAILED.value,
            JobStatus.QUEUED.value,  # recoverable auto-retry / expired lease
        }
    ),
    JobStatus.FINALIZING.value: frozenset(
        {
            JobStatus.COMPLETED.value,
            JobStatus.CANCELLED.value,
            JobStatus.FAILED.value,
            JobStatus.QUEUED.value,  # expired lease recovery
        }
    ),
    JobStatus.COMPLETED.value: frozenset(),
    JobStatus.FAILED.value: frozenset(
        {
            JobStatus.QUEUED.value,  # explicit retry
        }
    ),
    JobStatus.CANCELLED.value: frozenset(
        {
            JobStatus.QUEUED.value,  # explicit retry
        }
    ),
}


def can_transition(from_status: str | None, to_status: str) -> bool:
    if not from_status:
        return to_status == JobStatus.QUEUED.value
    if from_status == to_status:
        return True
    allowed = LEGAL_TRANSITIONS.get(from_status)
    if allowed is None:
        return False
    return to_status in allowed


def assert_transition(from_status: str | None, to_status: str) -> None:
    if not can_transition(from_status, to_status):
        raise ValueError(f"Illegal job transition: {from_status!r} -> {to_status!r}")
