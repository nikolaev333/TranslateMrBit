"""Inference backend abstraction for TranslateMrBit."""

from app.inference.backend_registry import get_backend, list_backends
from app.inference.model_profiles import MODEL_PROFILES, get_model_profile

__all__ = [
    "MODEL_PROFILES",
    "get_backend",
    "get_model_profile",
    "list_backends",
]
