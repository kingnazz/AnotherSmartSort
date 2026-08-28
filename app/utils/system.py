"""Small OS integration helpers (opening folders, elapsed-time formatting)."""

from __future__ import annotations

from pathlib import Path

from app.utils.external_process import open_path_in_file_manager
from app.utils.logging_setup import get_logger

logger = get_logger("system")


def open_in_file_manager(path: str | Path) -> bool:
    """Reveal a folder (or a file's folder) in the OS file manager.

    Delegates to :mod:`app.utils.external_process` so every process this
    application starts goes through one audited place -- including this one,
    which must not leave a stray console window behind either.
    """
    return open_path_in_file_manager(path)


def format_duration(seconds: float) -> str:
    """``9.4s`` / ``2m 05s`` / ``1h 04m``."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {remainder:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """``1 PDF`` / ``3 PDFs``."""
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


__all__ = ["open_in_file_manager", "format_duration", "plural"]
