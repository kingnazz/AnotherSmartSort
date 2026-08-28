"""Structured application logging.

Privacy rules enforced here:

* API keys and passwords are never logged -- :class:`RedactingFilter` scrubs
  anything that looks like a secret even if a caller is careless.
* Full document text is never logged. Callers log page indexes, character
  counts and decisions, not content.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from typing import Any

from .paths import log_file_path

_CONFIGURED = False

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret|authorization)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
]

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s"


class RedactingFilter(logging.Filter):
    """Scrub secrets from log records as a defence-in-depth measure."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(level: int | None = None, console: bool = True) -> logging.Logger:
    """Configure rotating file + console logging. Safe to call more than once."""
    global _CONFIGURED
    root = logging.getLogger("smartpdfsorter")
    if _CONFIGURED:
        return root

    env_level = os.environ.get("SMART_PDF_SORTER_LOG_LEVEL", "").upper()
    resolved = level if level is not None else getattr(logging, env_level, logging.INFO)
    root.setLevel(resolved)
    root.propagate = False

    formatter = logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    redactor = RedactingFilter()

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file_path(), maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)
    except OSError:
        # A read-only or unavailable data directory must not stop the app.
        console = True

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream.addFilter(redactor)
        root.addHandler(stream)

    _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(f"smartpdfsorter.{name}")


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Log a structured key=value event line."""
    parts = " ".join(f"{key}={value!r}" for key, value in fields.items() if value is not None)
    logger.info("%s %s", event, parts) if parts else logger.info("%s", event)


__all__ = ["configure_logging", "get_logger", "log_event", "RedactingFilter", "log_file_path"]
