"""Future queue backends (not active in the current SQLite single-host deploy).

SQLite atomic claims are correct for one server sharing one database file.
Multi-host deployment requires shared durable storage + a coordinated queue —
this module documents the adapter surface without claiming it is implemented.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class JobQueueAdapter(ABC):
    """Port for claiming / releasing jobs across storage backends."""

    @abstractmethod
    async def claim_next(
        self,
        *,
        worker_id: str,
        max_concurrent: int,
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    async def release(self, job_id: str, *, reason: str) -> None: ...

    @abstractmethod
    async def heartbeat(self, worker_id: str, job_id: str | None) -> None: ...


class SqliteJobQueueAdapter(JobQueueAdapter):
    """Thin wrapper around the existing Database claim helpers."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def claim_next(
        self,
        *,
        worker_id: str,
        max_concurrent: int,
    ) -> dict[str, Any] | None:
        return await self.db.claim_next_job(
            worker_id=worker_id,
            max_concurrent=max_concurrent,
        )

    async def release(self, job_id: str, *, reason: str) -> None:
        await self.db.update_job(
            job_id,
            status="queued",
            worker_id=None,
            progress_message=reason,
            processing_stage="queued",
            lease_expires_at=None,
        )

    async def heartbeat(self, worker_id: str, job_id: str | None) -> None:
        await self.db.upsert_worker_heartbeat(
            worker_id,
            status="busy" if job_id else "idle",
            current_job_id=job_id,
        )


# Planned (not implemented): PostgresJobQueueAdapter / RedisJobQueueAdapter
# would require shared object storage for uploads/outputs, a single logical
# queue, and lease fencing across hosts. Do not enable until tested.
