"""Documented approximate Whisper model resource profiles.

Estimates are guidance only — not guaranteed hardware requirements.
Observed usage from completed jobs may refine recommendations later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelProfile:
    model_id: str
    backends: tuple[str, ...]
    devices: tuple[str, ...]
    precision_options: tuple[str, ...]
    # Approximate peak RAM for openai-whisper FP32 CPU (GB) — guidance only
    approx_ram_gb_cpu: float
    approx_vram_gb_cuda: float
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Guidance values derived from public Whisper docs / community observations.
MODEL_PROFILES: dict[str, ModelProfile] = {
    "tiny": ModelProfile(
        "tiny",
        ("openai-whisper", "faster-whisper"),
        ("cpu", "cuda"),
        ("fp32", "fp16", "int8"),
        1.0,
        1.0,
        "Smallest; lowest quality",
    ),
    "base": ModelProfile(
        "base",
        ("openai-whisper", "faster-whisper"),
        ("cpu", "cuda"),
        ("fp32", "fp16", "int8"),
        1.2,
        1.0,
        "Light CPU demos",
    ),
    "small": ModelProfile(
        "small",
        ("openai-whisper", "faster-whisper"),
        ("cpu", "cuda"),
        ("fp32", "fp16", "int8"),
        2.5,
        2.0,
        "Recommended for ~4GB demo VPS",
    ),
    "medium": ModelProfile(
        "medium",
        ("openai-whisper", "faster-whisper"),
        ("cpu", "cuda"),
        ("fp32", "fp16", "int8"),
        5.5,
        5.0,
        "Needs substantial RAM",
    ),
    "large": ModelProfile(
        "large",
        ("openai-whisper", "faster-whisper"),
        ("cpu", "cuda"),
        ("fp32", "fp16", "int8"),
        10.0,
        10.0,
        "Needs ~8GB+ free RAM on CPU; GPU preferred",
    ),
    "turbo": ModelProfile(
        "turbo",
        ("openai-whisper",),
        ("cpu", "cuda"),
        ("fp32", "fp16"),
        6.0,
        6.0,
        "openai-whisper turbo variant",
    ),
}


def get_model_profile(model_id: str) -> ModelProfile | None:
    return MODEL_PROFILES.get(model_id)


def list_model_profiles() -> list[dict[str, Any]]:
    return [p.to_dict() for p in MODEL_PROFILES.values()]
