"""Structured logging helpers shared by API and worker processes."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any


_CONFIGURED = False


def setup_logging(service: str = "whisper_api") -> logging.Logger:
    """Configure root logging once; return a service-scoped logger."""
    global _CONFIGURED
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    if not _CONFIGURED:
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stdout,
            force=True,
        )
        _CONFIGURED = True

    logger = logging.getLogger(service)
    logger.setLevel(level)
    return logger


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit a single-line structured event for grepping in Coolify logs."""
    parts = [f"event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).replace("\n", " ").replace("\r", " ")
        if " " in text or "=" in text:
            text = repr(text)
        parts.append(f"{key}={text}")
    logger.info(" ".join(parts))
