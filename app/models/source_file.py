"""Analysis state for a single source PDF in the queue."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .document import DocumentGroup
from .enums import FileStatus
from .packet import CandidatePacket
from .page import PageAnalysis


@dataclass
class SourceFileAnalysis:
    """One queued PDF: its pages, its documents, its candidates, and its status."""

    path: Path
    page_count: int = 0
    status: FileStatus = FileStatus.WAITING
    pages: list[PageAnalysis] = field(default_factory=list)
    groups: list[DocumentGroup] = field(default_factory=list)
    #: Candidate packets reconstructed from :attr:`groups`.
    packets: list[CandidatePacket] = field(default_factory=list)

    error: str | None = None
    content_hash: str | None = None
    duplicate_of: str | None = None
    encrypted: bool = False

    ocr_pages: int = 0
    #: Pages whose text came from the PDF itself, needing no OCR at all. Read
    #: alongside :attr:`ocr_pages`: a native-text file showing a high OCR count
    #: is doing avoidable work, which is slow and (on Windows) the thing that
    #: used to flash a console window per page.
    native_text_pages: int = 0
    #: Pages where OCR ran but produced nothing usable.
    ocr_failures: int = 0
    ai_pages: int = 0
    ai_requests: int = 0
    analysis_seconds: float = 0.0

    #: Which structured parser handled this file, if any. Empty means the
    #: generic pipeline did, which is normal for an unrecognised format.
    parser_name: str = ""
    #: How sure the parser was that the file matches its format -- a separate
    #: question from how sure it is about any one document's type, and the
    #: reason a recognised file no longer reports misleading low confidence.
    structure_confidence: float = 0.0
    #: Problems the parser recognised but could not resolve (a roster count
    #: that disagrees with what was found, a missing section ending). Surfaced
    #: rather than swallowed: a silent partial success loses documents.
    parser_warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def display_size(self) -> str:
        try:
            size = self.path.stat().st_size
        except OSError:
            return "—"
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} GB"

    @property
    def is_analyzed(self) -> bool:
        return self.status in (
            FileStatus.READY,
            FileStatus.REVIEW_NEEDED,
            FileStatus.COMPLETED,
            FileStatus.EXPORTING,
        )

    @property
    def active_groups(self) -> list[DocumentGroup]:
        """Groups that will actually be exported."""
        return [g for g in self.groups if not g.excluded and g.export_page_indexes]

    @property
    def review_group_count(self) -> int:
        return sum(1 for g in self.groups if g.needs_attention and not g.excluded)

    @property
    def identified_packets(self) -> list[CandidatePacket]:
        """Packets belonging to a named candidate, excluding the unknown queue."""
        return [p for p in self.packets if not p.is_unknown]

    @property
    def unknown_packet(self) -> CandidatePacket | None:
        return next((p for p in self.packets if p.is_unknown), None)

    @property
    def candidate_count(self) -> int:
        return len(self.identified_packets)

    def packet(self, packet_id: str | None) -> CandidatePacket | None:
        if not packet_id:
            return None
        return next((p for p in self.packets if p.id == packet_id), None)

    def packet_for_document(self, group: DocumentGroup) -> CandidatePacket | None:
        return self.packet(group.packet_id)

    @property
    def lowest_confidence(self) -> float | None:
        confidences = [g.overall_confidence for g in self.groups if not g.excluded]
        return min(confidences) if confidences else None

    def page(self, page_index: int) -> PageAnalysis | None:
        for page in self.pages:
            if page.page_index == page_index:
                return page
        return None

    def group_for_page(self, page_index: int) -> DocumentGroup | None:
        for group in self.groups:
            if group.contains(page_index):
                return group
        return None

    def refresh_status(self) -> None:
        """Recompute Ready / Review Needed after analysis or a user correction."""
        if self.status in (FileStatus.ERROR, FileStatus.COMPLETED, FileStatus.EXPORTING):
            return
        if not self.groups:
            return
        self.status = (
            FileStatus.REVIEW_NEEDED if self.review_group_count else FileStatus.READY
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "name": self.name,
            "page_count": self.page_count,
            "status": self.status.value,
            "error": self.error,
            "content_hash": self.content_hash,
            "duplicate_of": self.duplicate_of,
            "encrypted": self.encrypted,
            "ocr_pages": self.ocr_pages,
            "native_text_pages": self.native_text_pages,
            "ocr_failures": self.ocr_failures,
            "parser_name": self.parser_name,
            "structure_confidence": round(self.structure_confidence, 4),
            "parser_warnings": list(self.parser_warnings),
            "ai_pages": self.ai_pages,
            "ai_requests": self.ai_requests,
            "groups": [g.to_dict() for g in self.groups],
            "packets": [p.to_dict() for p in self.packets],
        }


def unique_display_names(files: list[SourceFileAnalysis]) -> dict[str, str]:
    """Map file path -> display name, disambiguating identical filenames."""
    by_name: dict[str, list[SourceFileAnalysis]] = {}
    for item in files:
        by_name.setdefault(item.name, []).append(item)

    result: dict[str, str] = {}
    for name, items in by_name.items():
        if len(items) == 1:
            result[str(items[0].path)] = name
            continue
        for item in items:
            parent = item.path.parent.name or str(item.path.parent)
            result[str(item.path)] = f"{name}  ·  {parent}{os.sep}"
    return result


__all__ = ["SourceFileAnalysis", "unique_display_names"]
