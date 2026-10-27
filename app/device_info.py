"""Device label helpers that never import torch."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings


def configured_device_label(settings: "Settings") -> str:
    """Return configured device without importing torch (safe for /health)."""
    configured = (settings.whisper_device or "auto").lower().strip()
    if configured in {"cpu", "cuda", "auto"}:
        return configured
    return "auto"
