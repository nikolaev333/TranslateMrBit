"""Resource-aware job admission / scheduling policy (deterministic, testable)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.inference.model_profiles import MODEL_PROFILES
from app.services.smart_processing.resource_monitor import effective_available_ram_gb


@dataclass
class AdmissionDecision:
    admit: bool
    reason: str
    code: str  # ready | waiting_resources | blocked_config | concurrency | cancelled

    def to_dict(self) -> dict[str, Any]:
        return {"admit": self.admit, "reason": self.reason, "code": self.code}


def estimate_job_memory_gb(model_name: str, backend: str = "openai-whisper") -> float:
    profile = MODEL_PROFILES.get(model_name)
    if not profile:
        return 4.0
    est = profile.approx_ram_gb_cpu
    if backend == "faster-whisper":
        est = max(0.5, est * 0.65)
    return est


def can_admit_job(
    job: dict[str, Any],
    *,
    running_count: int,
    max_concurrent: int,
    resources: dict[str, Any] | None,
    safety_margin_gb: float = 0.75,
    enforce_memory_gate: bool = True,
) -> AdmissionDecision:
    """Decide whether a queued job may enter processing now."""
    if job.get("cancel_requested"):
        return AdmissionDecision(False, "Cancellation requested", "cancelled")

    if running_count >= max(1, max_concurrent):
        return AdmissionDecision(
            False,
            f"Concurrency limit reached ({running_count}/{max_concurrent})",
            "concurrency",
        )

    model = job.get("model_name") or "small"
    backend = (job.get("inference_backend") or "openai-whisper").lower()
    if model not in MODEL_PROFILES and not str(model).startswith("large"):
        # Allow unknown names but flag
        pass

    if not enforce_memory_gate:
        return AdmissionDecision(True, "Admitted (memory gate disabled)", "ready")

    avail = effective_available_ram_gb(resources)
    need = estimate_job_memory_gb(str(model), backend)
    if avail is None:
        # Conservative: allow tiny/base/small only when unknown
        if str(model) in {"tiny", "base", "small"}:
            return AdmissionDecision(
                True,
                "Admitted under insufficient evidence for small models only",
                "ready",
            )
        return AdmissionDecision(
            False,
            "Waiting: cannot measure available RAM for larger models",
            "waiting_resources",
        )

    if avail >= need + safety_margin_gb:
        return AdmissionDecision(
            True,
            f"Resources OK (~{avail:.1f}GB avail, ~{need:.1f}GB estimate)",
            "ready",
        )
    return AdmissionDecision(
        False,
        f"Waiting for resources (~{avail:.1f}GB avail, need ~{need:.1f}GB + {safety_margin_gb:.2f}GB margin)",
        "waiting_resources",
    )


def sort_queue(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Higher priority first, then older jobs (anti-starvation by age)."""

    def key(j: dict[str, Any]) -> tuple:
        pri = int(j.get("priority") or 100)
        created = j.get("created_at") or ""
        return (-pri, created)

    return sorted(jobs, key=key)
