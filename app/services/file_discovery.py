"""Discovering PDFs from files, folders and drag-and-drop payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from app.utils.logging_setup import get_logger

logger = get_logger("discovery")

PDF_SUFFIXES = (".pdf",)

#: Folder names never treated as *sources*, so a previous run's output does not
#: get fed back in as input.
_SKIPPED_DIR_NAMES = {
    "smart pdf sorter output",
    "__pycache__",
    ".git",
    "$recycle.bin",
    "system volume information",
}


def is_pdf(path: str | Path) -> bool:
    return Path(path).suffix.lower() in PDF_SUFFIXES


def discover_pdfs(
    inputs: Iterable[str | Path],
    *,
    include_subfolders: bool = True,
    exclude_dirs: Sequence[str | Path] = (),
) -> list[Path]:
    """Expand a mix of files and folders into a sorted, de-duplicated PDF list.

    Output directories are skipped so re-running a job never re-ingests results.
    """
    excluded = {_resolve(Path(directory)) for directory in exclude_dirs if str(directory).strip()}
    found: dict[str, Path] = {}

    for raw in inputs:
        path = Path(raw)
        try:
            if path.is_file():
                if is_pdf(path) and not _is_inside(path, excluded):
                    found[str(_resolve(path))] = path
            elif path.is_dir():
                for pdf in _walk_folder(path, include_subfolders, excluded):
                    found[str(_resolve(pdf))] = pdf
        except OSError as exc:
            logger.warning("Could not read %s: %s", path, exc)

    return sorted(found.values(), key=lambda p: (str(p.parent).lower(), p.name.lower()))


def _walk_folder(
    folder: Path, include_subfolders: bool, excluded: set[Path]
) -> list[Path]:
    if _is_inside(folder, excluded) or _is_skipped_dir(folder):
        return []

    results: list[Path] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        logger.warning("Could not list %s: %s", folder, exc)
        return results

    for entry in entries:
        try:
            if entry.is_file() and is_pdf(entry):
                results.append(entry)
            elif entry.is_dir() and include_subfolders:
                results.extend(_walk_folder(entry, include_subfolders, excluded))
        except OSError:
            continue
    return results


def _is_skipped_dir(path: Path) -> bool:
    return path.name.lower() in _SKIPPED_DIR_NAMES


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _is_inside(path: Path, directories: set[Path]) -> bool:
    if not directories:
        return False
    resolved = _resolve(path)
    for directory in directories:
        try:
            # Succeeds only when `resolved` sits inside `directory`.
            resolved.relative_to(directory)
            return True
        except ValueError:
            continue
    return False


__all__ = ["discover_pdfs", "is_pdf", "PDF_SUFFIXES"]
