"""Processing job model: one analyze/export run over a set of source PDFs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .enums import JobStatus


@dataclass
class JobError:
    """A recoverable failure recorded during a job. One bad PDF never kills a batch."""

    source: str
    message: str
    detail: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "message": self.message,
            "detail": self.detail,
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
        }


@dataclass
class ProcessingJob:
    """Aggregate counters and outcome for one analyze + export run."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    inputs: list[str] = field(default_factory=list)
    output_directory: str | None = None
    status: JobStatus = JobStatus.PENDING

    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None

    pdfs_processed: int = 0
    pages_processed: int = 0
    documents_found: int = 0
    documents_exported: int = 0
    review_documents: int = 0
    candidates_found: int = 0
    packets_exported: int = 0

    pages_classified_locally: int = 0
    pages_classified_by_ai: int = 0
    ai_requests: int = 0
    ocr_pages: int = 0

    errors: list[JobError] = field(default_factory=list)
    excel_index_path: str | None = None

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or datetime.now()
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def add_error(self, source: str | Path, message: str, detail: str | None = None) -> JobError:
        error = JobError(source=str(source), message=message, detail=detail)
        self.errors.append(error)
        return error

    def finish(self, status: JobStatus) -> None:
        self.status = status
        self.finished_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "inputs": list(self.inputs),
            "output_directory": self.output_directory,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": self.finished_at.isoformat(timespec="seconds")
            if self.finished_at
            else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "pdfs_processed": self.pdfs_processed,
            "pages_processed": self.pages_processed,
            "documents_found": self.documents_found,
            "documents_exported": self.documents_exported,
            "candidates_found": self.candidates_found,
            "packets_exported": self.packets_exported,
            "review_documents": self.review_documents,
            "pages_classified_locally": self.pages_classified_locally,
            "pages_classified_by_ai": self.pages_classified_by_ai,
            "ai_requests": self.ai_requests,
            "ocr_pages": self.ocr_pages,
            "excel_index_path": self.excel_index_path,
            "errors": [e.to_dict() for e in self.errors],
        }


__all__ = ["ProcessingJob", "JobError"]
