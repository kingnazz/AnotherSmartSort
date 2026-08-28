"""Local processing history (SQLite).

Only job metadata is persisted -- counters, paths, and content hashes for
duplicate detection. Extracted document text and applicant details are never
written here, in line with the privacy requirements.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from app.models.processing_job import ProcessingJob
from app.models.source_file import SourceFileAnalysis
from app.utils.logging_setup import get_logger
from app.utils.paths import history_db_path

logger = get_logger("history")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    output_directory    TEXT,
    status              TEXT,
    pdfs_processed      INTEGER DEFAULT 0,
    pages_processed     INTEGER DEFAULT 0,
    documents_found     INTEGER DEFAULT 0,
    documents_exported  INTEGER DEFAULT 0,
    review_documents    INTEGER DEFAULT 0,
    pages_local         INTEGER DEFAULT 0,
    pages_ai            INTEGER DEFAULT 0,
    ai_requests         INTEGER DEFAULT 0,
    ocr_pages           INTEGER DEFAULT 0,
    error_count         INTEGER DEFAULT 0,
    error_summary       TEXT,
    duration_seconds    REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_sources (
    job_id          TEXT NOT NULL,
    path            TEXT NOT NULL,
    name            TEXT NOT NULL,
    content_hash    TEXT,
    page_count      INTEGER DEFAULT 0,
    documents       INTEGER DEFAULT 0,
    status          TEXT,
    PRIMARY KEY (job_id, path),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_job_sources_hash ON job_sources(content_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs(started_at DESC);
"""


@dataclass
class HistoryEntry:
    """One previously executed job, as shown on the History screen."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    output_directory: str | None
    status: str
    pdfs_processed: int
    pages_processed: int
    documents_found: int
    documents_exported: int
    review_documents: int
    pages_local: int
    pages_ai: int
    ai_requests: int
    ocr_pages: int
    error_count: int
    error_summary: str
    duration_seconds: float
    sources: list[str] = field(default_factory=list)

    @property
    def display_time(self) -> str:
        return self.started_at.strftime("%d %b %Y, %H:%M")

    @property
    def summary(self) -> str:
        parts = [
            f"{self.pdfs_processed} PDF{'s' if self.pdfs_processed != 1 else ''}",
            f"{self.pages_processed} pages",
            f"{self.documents_found} documents",
        ]
        if self.documents_exported:
            parts.append(f"{self.documents_exported} exported")
        if self.error_count:
            parts.append(f"{self.error_count} error{'s' if self.error_count != 1 else ''}")
        return " · ".join(parts)


class HistoryStore:
    """SQLite-backed job history and duplicate index."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else history_db_path()
        self._available = True
        self._initialise()

    # ------------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialise(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
        except (sqlite3.Error, OSError) as exc:
            # History is a convenience; losing it must never stop processing.
            self._available = False
            logger.warning("Processing history is unavailable: %s", exc)

    @property
    def is_available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    def record_job(self, job: ProcessingJob, files: Sequence[SourceFileAnalysis]) -> bool:
        """Persist a completed job and the files it touched."""
        if not self._available:
            return False

        error_summary = "; ".join(error.message for error in job.errors[:5])
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO jobs (
                        id, started_at, finished_at, output_directory, status,
                        pdfs_processed, pages_processed, documents_found,
                        documents_exported, review_documents, pages_local, pages_ai,
                        ai_requests, ocr_pages, error_count, error_summary, duration_seconds
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        job.id,
                        job.started_at.isoformat(timespec="seconds"),
                        job.finished_at.isoformat(timespec="seconds") if job.finished_at else None,
                        job.output_directory,
                        job.status.value,
                        job.pdfs_processed,
                        job.pages_processed,
                        job.documents_found,
                        job.documents_exported,
                        job.review_documents,
                        job.pages_classified_locally,
                        job.pages_classified_by_ai,
                        job.ai_requests,
                        job.ocr_pages,
                        len(job.errors),
                        error_summary,
                        round(job.duration_seconds, 2),
                    ),
                )
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO job_sources
                        (job_id, path, name, content_hash, page_count, documents, status)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            job.id,
                            str(file.path),
                            file.name,
                            file.content_hash,
                            file.page_count,
                            len(file.groups),
                            file.status.value,
                        )
                        for file in files
                    ],
                )
            return True
        except sqlite3.Error as exc:
            logger.warning("Could not record job history: %s", exc)
            return False

    # ------------------------------------------------------------------
    def recent_jobs(self, limit: int = 50) -> list[HistoryEntry]:
        if not self._available:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,)
                ).fetchall()
                entries = [self._to_entry(row) for row in rows]
                for entry in entries:
                    sources = connection.execute(
                        "SELECT name FROM job_sources WHERE job_id = ? ORDER BY name", (entry.id,)
                    ).fetchall()
                    entry.sources = [source["name"] for source in sources]
                return entries
        except sqlite3.Error as exc:
            logger.warning("Could not read job history: %s", exc)
            return []

    def known_hashes(self) -> dict[str, str]:
        """Map content hash -> the filename it was first seen as."""
        if not self._available:
            return {}
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT content_hash, name FROM job_sources "
                    "WHERE content_hash IS NOT NULL AND content_hash != ''"
                ).fetchall()
            return {row["content_hash"]: row["name"] for row in rows}
        except sqlite3.Error as exc:
            logger.warning("Could not read duplicate index: %s", exc)
            return {}

    def find_duplicates(self, hashes: Iterable[str]) -> dict[str, str]:
        """Return only the supplied hashes that were seen before."""
        known = self.known_hashes()
        return {value: known[value] for value in hashes if value and value in known}

    def clear(self) -> bool:
        if not self._available:
            return False
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM job_sources")
                connection.execute("DELETE FROM jobs")
            return True
        except sqlite3.Error as exc:
            logger.warning("Could not clear history: %s", exc)
            return False

    # ------------------------------------------------------------------
    @staticmethod
    def _to_entry(row: sqlite3.Row) -> HistoryEntry:
        def _parse(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        return HistoryEntry(
            id=row["id"],
            started_at=_parse(row["started_at"]) or datetime.now(),
            finished_at=_parse(row["finished_at"]),
            output_directory=row["output_directory"],
            status=row["status"] or "",
            pdfs_processed=row["pdfs_processed"] or 0,
            pages_processed=row["pages_processed"] or 0,
            documents_found=row["documents_found"] or 0,
            documents_exported=row["documents_exported"] or 0,
            review_documents=row["review_documents"] or 0,
            pages_local=row["pages_local"] or 0,
            pages_ai=row["pages_ai"] or 0,
            ai_requests=row["ai_requests"] or 0,
            ocr_pages=row["ocr_pages"] or 0,
            error_count=row["error_count"] or 0,
            error_summary=row["error_summary"] or "",
            duration_seconds=row["duration_seconds"] or 0.0,
        )


__all__ = ["HistoryStore", "HistoryEntry"]
