"""Platform-appropriate application data locations.

No user paths are ever hard-coded: everything resolves from the OS environment
so the packaged EXE behaves correctly on any machine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app import APP_NAME, ORG_NAME

#: Redirects every application data path, for portable use and for tests.
#: Kept under its original name through the rename to "AS Resume Sorter": it is
#: a documented deployment knob, and renaming it would silently stop honouring
#: the variable existing scripts already set.
_ENV_OVERRIDE = "SMART_PDF_SORTER_HOME"


def _base_data_dir() -> Path:
    """Root directory for application data, honouring an environment override."""
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override).expanduser()

    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
        return base / ORG_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / ORG_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / ORG_NAME


def app_data_dir() -> Path:
    path = _base_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def history_db_path() -> Path:
    return app_data_dir() / "history.sqlite3"


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file_path() -> Path:
    return logs_dir() / "smart_pdf_sorter.log"


def cache_dir() -> Path:
    path = app_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    """Default export destination: Documents/AS Resume Sorter.

    Only ever used when nothing is configured yet. An existing installation
    keeps whatever folder it already had -- that choice lives in settings.json
    and is not re-derived, so this rename cannot move anybody's output.
    """
    if sys.platform.startswith("win"):
        documents = Path.home() / "Documents"
    else:
        documents = Path.home() / "Documents"
        if not documents.exists():
            documents = Path.home()
    return documents / "AS Resume Sorter"


def resource_path(*parts: str) -> Path:
    """Resolve a bundled asset, working both from source and inside PyInstaller."""
    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) if bundle else Path(__file__).resolve().parents[2]
    return root.joinpath(*parts)


def app_display_name() -> str:
    return APP_NAME


__all__ = [
    "app_data_dir",
    "settings_path",
    "history_db_path",
    "logs_dir",
    "log_file_path",
    "cache_dir",
    "default_output_dir",
    "resource_path",
    "app_display_name",
]
