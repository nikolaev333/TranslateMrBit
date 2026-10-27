"""Cross-process preload request/status files (API <-> worker). No torch."""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REQUEST_FILENAME = "preload_request.json"
STATUS_FILENAME = "preload_status.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def control_dir(data_dir: str | Path, db_path: str | Path | None = None) -> Path:
    """Prefer the SQLite directory — it is already a shared persistent volume."""
    if db_path:
        return Path(db_path).parent
    return Path(data_dir)


def request_path(data_dir: str | Path, db_path: str | Path | None = None) -> Path:
    return control_dir(data_dir, db_path) / REQUEST_FILENAME


def status_path(data_dir: str | Path, db_path: str | Path | None = None) -> Path:
    return control_dir(data_dir, db_path) / STATUS_FILENAME


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    ) as tmp:
        tmp.write(raw)
        tmp_name = tmp.name
    Path(tmp_name).replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def write_preload_request(
    data_dir: str | Path,
    models: list[str],
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    payload = {
        "models": list(models),
        "requested_at": _utc_now(),
    }
    _atomic_write(request_path(data_dir, db_path), payload)
    return payload


def read_preload_request(
    data_dir: str | Path,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    return _read_json(request_path(data_dir, db_path))


def clear_preload_request(
    data_dir: str | Path,
    db_path: str | Path | None = None,
) -> None:
    path = request_path(data_dir, db_path)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to clear preload request: %s", exc)


def write_preload_status(
    data_dir: str | Path,
    status: dict[str, Any],
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    payload = dict(status)
    payload["updated_at"] = _utc_now()
    _atomic_write(status_path(data_dir, db_path), payload)
    return payload


def read_preload_status(
    data_dir: str | Path,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    data = _read_json(status_path(data_dir, db_path))
    if not data:
        return {
            "running": False,
            "current": None,
            "requested": [],
            "completed": [],
            "failed": {},
            "updated_at": None,
        }
    return data


def default_idle_status() -> dict[str, Any]:
    return {
        "running": False,
        "current": None,
        "requested": [],
        "completed": [],
        "failed": {},
    }
