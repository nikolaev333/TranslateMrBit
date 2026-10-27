"""Disk-only Whisper checkpoint helpers — never imports torch/whisper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Official openai-whisper checkpoint filenames.
CHECKPOINT_FILES = {
    "tiny": "tiny.pt",
    "base": "base.pt",
    "small": "small.pt",
    "medium": "medium.pt",
    "large": "large-v3.pt",
    "turbo": "large-v3-turbo.pt",
    "tiny.en": "tiny.en.pt",
    "base.en": "base.en.pt",
    "small.en": "small.en.pt",
    "medium.en": "medium.en.pt",
    "large-v1": "large-v1.pt",
    "large-v2": "large-v2.pt",
    "large-v3": "large-v3.pt",
}


def ensure_model_dir(model_dir: str | Path) -> Path:
    root = Path(model_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def model_checkpoint_path(model_dir: str | Path, model_name: str) -> Path | None:
    filename = CHECKPOINT_FILES.get(model_name)
    if not filename:
        return None
    return ensure_model_dir(model_dir) / filename


def is_model_downloaded(model_dir: str | Path, model_name: str) -> bool:
    path = model_checkpoint_path(model_dir, model_name)
    return bool(path and path.is_file() and path.stat().st_size > 0)


def list_model_download_status(
    model_dir: str | Path,
    names: tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in names:
        path = model_checkpoint_path(model_dir, name)
        size = path.stat().st_size if path and path.is_file() else 0
        out.append(
            {
                "name": name,
                "downloaded": bool(path and path.is_file() and size > 0),
                "path": str(path) if path else None,
                "size_bytes": size,
            }
        )
    return out
