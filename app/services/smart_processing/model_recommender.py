"""Hardware-aware model recommendations (never silently swaps models)."""

from __future__ import annotations

from typing import Any

from app.inference.model_profiles import MODEL_PROFILES, ModelProfile
from app.services.smart_processing.resource_monitor import (
    collect_resources,
    effective_available_ram_gb,
)


def recommend_models(
    *,
    device: str = "cpu",
    backend: str = "openai-whisper",
    safety_margin_gb: float = 0.75,
    resources: dict[str, Any] | None = None,
    installed_models: set[str] | None = None,
    observed_usage_gb: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Return ranked recommendations with explanations.

    Does not select a model for the user. Unsafe configs are flagged, not substituted.
    """
    resources = resources or collect_resources()
    avail = effective_available_ram_gb(resources)
    device = (device or "cpu").lower()
    backend = (backend or "openai-whisper").lower()
    installed_models = installed_models or set()
    observed_usage_gb = observed_usage_gb or {}

    recommendations: list[dict[str, Any]] = []
    for model_id, profile in MODEL_PROFILES.items():
        if backend not in profile.backends and backend != "any":
            continue
        if device not in profile.devices:
            continue
        est = observed_usage_gb.get(model_id)
        if est is None:
            est = (
                profile.approx_vram_gb_cuda
                if device == "cuda"
                else profile.approx_ram_gb_cpu
            )
        # faster-whisper INT8 on CPU often needs less — treat as soft discount, not guarantee
        if backend == "faster-whisper" and device == "cpu":
            est = max(0.5, est * 0.65)

        status = "unknown"
        reason = "Insufficient resource measurements to certify safety."
        if avail is None:
            status = "insufficient_evidence"
        elif avail >= est + safety_margin_gb:
            status = "recommended"
            reason = (
                f"Approx need ~{est:.1f}GB + {safety_margin_gb:.2f}GB margin; "
                f"effective available ~{avail:.1f}GB."
            )
        elif avail >= est:
            status = "marginal"
            reason = (
                f"Approx need ~{est:.1f}GB close to available ~{avail:.1f}GB "
                f"(margin {safety_margin_gb:.2f}GB not met)."
            )
        else:
            status = "unsafe"
            reason = (
                f"Approx need ~{est:.1f}GB exceeds effective available ~{avail:.1f}GB. "
                "Do not silently substitute another model."
            )

        recommendations.append(
            {
                "model_id": model_id,
                "backend": backend,
                "device": device,
                "status": status,
                "approx_memory_gb": round(est, 2),
                "available_gb": None if avail is None else round(avail, 2),
                "installed": model_id in installed_models,
                "explanation": reason,
                "notes": profile.notes,
                "estimate_source": "observed" if model_id in observed_usage_gb else "documented_guidance",
            }
        )

    order = {"recommended": 0, "marginal": 1, "insufficient_evidence": 2, "unsafe": 3, "unknown": 4}
    recommendations.sort(key=lambda r: (order.get(r["status"], 9), r["approx_memory_gb"]))

    preferred = next((r for r in recommendations if r["status"] == "recommended"), None)
    return {
        "available_ram_gb": None if avail is None else round(avail, 2),
        "safety_margin_gb": safety_margin_gb,
        "device": device,
        "backend": backend,
        "preferred": preferred,
        "recommendations": recommendations,
        "disclaimer": (
            "Memory figures are approximate guidance or prior observations — "
            "not a guarantee that a job will succeed."
        ),
    }


def validate_model_choice(
    model_id: str,
    *,
    device: str = "cpu",
    backend: str = "openai-whisper",
    safety_margin_gb: float = 0.75,
    allow_unsafe: bool = False,
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a user-selected model. Never substitutes a different model."""
    profile: ModelProfile | None = MODEL_PROFILES.get(model_id)
    if profile is None:
        return {
            "ok": False,
            "model_id": model_id,
            "reason": f"Unsupported model '{model_id}'",
            "code": "unknown_model",
        }
    report = recommend_models(
        device=device,
        backend=backend,
        safety_margin_gb=safety_margin_gb,
        resources=resources,
    )
    match = next((r for r in report["recommendations"] if r["model_id"] == model_id), None)
    if match is None:
        return {
            "ok": False,
            "model_id": model_id,
            "reason": "Model incompatible with selected backend/device profile",
            "code": "incompatible",
        }
    if match["status"] == "unsafe" and not allow_unsafe:
        return {
            "ok": False,
            "model_id": model_id,
            "reason": match["explanation"],
            "code": "unsafe_resources",
            "recommendation": report.get("preferred"),
        }
    return {
        "ok": True,
        "model_id": model_id,
        "status": match["status"],
        "reason": match["explanation"],
        "code": "ok",
        "warning": match["status"] in {"marginal", "unsafe", "insufficient_evidence"},
    }
