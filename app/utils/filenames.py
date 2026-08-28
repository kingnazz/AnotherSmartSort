"""Windows-safe filename construction, templating and de-duplication."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Mapping

#: Characters Windows forbids in file and directory names.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_UNDERSCORE = re.compile(r"_{2,}")
_MULTI_SPACE = re.compile(r"\s{2,}")

#: Device names Windows reserves regardless of extension.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

DEFAULT_TEMPLATE = "{candidate}_{document_type}"

SUPPORTED_VARIABLES: tuple[str, ...] = (
    "candidate",
    "document_type",
    "source_file",
    "date",
    "applicant_id",
    "sequence",
)

_MAX_STEM_LENGTH = 120


def sanitize_filename(value: str, fallback: str = "Untitled", spaces_to: str = "_") -> str:
    """Make ``value`` safe as a Windows file name *stem*.

    Strips forbidden characters, collapses separators, trims trailing dots and
    spaces (which Windows silently drops), and avoids reserved device names.
    """
    text = str(value or "").strip()
    text = _INVALID_CHARS.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    if spaces_to is not None:
        text = text.replace(" ", spaces_to)
    text = _MULTI_UNDERSCORE.sub("_", text)
    text = text.strip(" ._-")

    if not text:
        return fallback

    if text.split(".")[0].upper() in _RESERVED_NAMES:
        text = f"_{text}"

    if len(text) > _MAX_STEM_LENGTH:
        text = text[:_MAX_STEM_LENGTH].rstrip(" ._-")

    return text or fallback


def sanitize_folder_name(value: str, fallback: str = "Unknown") -> str:
    """Folder names keep spaces (``Benjamin Perez/``) but drop illegal characters."""
    text = str(value or "").strip()
    text = _INVALID_CHARS.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip(" .")
    if not text:
        return fallback
    if text.split(".")[0].upper() in _RESERVED_NAMES:
        text = f"_{text}"
    if len(text) > _MAX_STEM_LENGTH:
        text = text[:_MAX_STEM_LENGTH].strip(" .")
    return text or fallback


def render_filename_template(
    template: str,
    *,
    candidate: str | None = None,
    document_type: str | None = None,
    source_file: str | None = None,
    applicant_id: str | None = None,
    sequence: int | None = None,
    when: date | datetime | None = None,
) -> str:
    """Render a user filename template, dropping variables that have no value.

    Unknown placeholders are left intact rather than raising, and a template
    that renders empty falls back to a deterministic, useful name.
    """
    values: dict[str, str] = {
        "candidate": (candidate or "").strip(),
        "document_type": (document_type or "").strip(),
        "source_file": Path(source_file).stem if source_file else "",
        "applicant_id": (applicant_id or "").strip(),
        "sequence": f"{sequence:03d}" if sequence is not None else "",
        "date": (when or date.today()).strftime("%Y-%m-%d"),
    }

    def _substitute(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in values:
            return match.group(0)
        return values[key]

    rendered = re.sub(r"\{([a-zA-Z_]+)\}", _substitute, template or DEFAULT_TEMPLATE)
    stem = sanitize_filename(rendered, fallback="")

    if not stem:
        parts = [p for p in (values["candidate"], values["document_type"]) if p]
        stem = sanitize_filename("_".join(parts) or values["source_file"] or "Document")
    return stem


def unique_path(directory: Path, stem: str, suffix: str = ".pdf", *, taken: set[str] | None = None) -> Path:
    """Return a non-colliding path: ``Name.pdf`` -> ``Name_2.pdf`` -> ``Name_3.pdf``.

    ``taken`` lets a caller reserve names for files not yet written to disk, so
    a single export run never collides with itself. Reserved by full path, not
    bare filename: the same stem legitimately recurs across different output
    folders (``Resumes/Trevor Hollands.pdf`` and ``Cover Letters/Trevor
    Hollands.pdf`` are not a collision), and only an identical path is.
    """
    directory = Path(directory)
    reserved = taken if taken is not None else set()

    def _key(path: Path) -> str:
        return str(path).lower()

    def _is_free(path: Path) -> bool:
        return not path.exists() and _key(path) not in reserved

    candidate = directory / f"{stem}{suffix}"
    if _is_free(candidate):
        reserved.add(_key(candidate))
        return candidate

    index = 2
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if _is_free(candidate):
            reserved.add(_key(candidate))
            return candidate
        index += 1
        if index > 9999:  # pragma: no cover - pathological safety valve
            raise OSError(f"Could not find a free filename for {stem!r} in {directory}")


def template_preview(template: str, mapping: Mapping[str, str] | None = None) -> str:
    """Human-readable preview used by the Settings dialog."""
    sample = {
        "candidate": "Benjamin Perez",
        "document_type": "Resume",
        "source_file": "BenjaminPerezApplication.pdf",
        "applicant_id": "A-10482",
        "sequence": 1,
    }
    if mapping:
        sample.update(mapping)
    return (
        render_filename_template(
            template,
            candidate=str(sample["candidate"]),
            document_type=str(sample["document_type"]),
            source_file=str(sample["source_file"]),
            applicant_id=str(sample["applicant_id"]),
            sequence=int(sample["sequence"]),
        )
        + ".pdf"
    )


def describe_variables(variables: Iterable[str] = SUPPORTED_VARIABLES) -> str:
    return "  ".join(f"{{{name}}}" for name in variables)


__all__ = [
    "sanitize_filename",
    "sanitize_folder_name",
    "render_filename_template",
    "unique_path",
    "template_preview",
    "describe_variables",
    "DEFAULT_TEMPLATE",
    "SUPPORTED_VARIABLES",
]
