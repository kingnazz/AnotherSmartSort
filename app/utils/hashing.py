"""Content hashing for duplicate detection and AI response caching."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def hash_file(path: str | Path, *, algorithm: str = "sha256") -> str:
    """Stream a file through a hash so very large PDFs never load into RAM."""
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def hash_text(text: str, *, algorithm: str = "sha256") -> str:
    """Hash normalized text. Used to cache classification per unique page body."""
    digest = hashlib.new(algorithm)
    digest.update(" ".join((text or "").split()).encode("utf-8", errors="replace"))
    return digest.hexdigest()


def short_hash(value: str, length: int = 12) -> str:
    return value[:length]


__all__ = ["hash_file", "hash_text", "short_hash"]
