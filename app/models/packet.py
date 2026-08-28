"""Candidate packet model.

A :class:`CandidatePacket` is the answer to the third intelligence problem:
*which logical documents belong to the same applicant?* It sits above
:class:`~app.models.document.DocumentGroup` the way a group sits above a page.

One 80-page source PDF typically yields tens of documents belonging to fifteen
or twenty different people. The packet is what the reviewer actually thinks in
-- "Jane Smith, three documents, pages 1-6" -- and what gets exported as a
combined PDF.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from .candidate import Candidate
from .document import DocumentGroup

_packet_counter = itertools.count(1)

#: Identifier of the holding area for documents with no confident owner.
UNKNOWN_PACKET_ID = "packet-unknown"


def _new_packet_id() -> str:
    return f"pkt-{next(_packet_counter):06d}"


@dataclass
class CandidatePacket:
    """Every document belonging to one applicant within a single source PDF."""

    source_pdf: str
    candidate: Candidate = field(default_factory=Candidate)
    documents: list[DocumentGroup] = field(default_factory=list)
    id: str = field(default_factory=_new_packet_id)

    #: How sure we are this packet is a distinct applicant from the one before.
    boundary_confidence: float = 0.0
    boundary_reasons: list[str] = field(default_factory=list)

    #: True for the holding area of documents that could not be attributed.
    is_unknown: bool = False
    #: Set when a reviewer named or confirmed this candidate.
    manually_named: bool = False

    review_reasons: list[str] = field(default_factory=list)

    # -- naming ------------------------------------------------------------
    @property
    def display_name(self) -> str:
        if self.is_unknown:
            return "Unknown / Needs Assignment"
        return self.candidate.display_name

    @property
    def is_identified(self) -> bool:
        return not self.is_unknown and bool(self.candidate.name)

    # -- geometry ----------------------------------------------------------
    @property
    def page_indexes(self) -> list[int]:
        indexes: set[int] = set()
        for document in self.documents:
            indexes.update(document.page_indexes)
        return sorted(indexes)

    @property
    def page_count(self) -> int:
        return len(self.page_indexes)

    @property
    def start_page_index(self) -> int:
        indexes = self.page_indexes
        return indexes[0] if indexes else -1

    @property
    def end_page_index(self) -> int:
        indexes = self.page_indexes
        return indexes[-1] if indexes else -1

    @property
    def page_range_label(self) -> str:
        indexes = self.page_indexes
        if not indexes:
            return "No pages"
        if len(indexes) == 1:
            return f"Page {indexes[0] + 1}"
        return f"Pages {indexes[0] + 1}–{indexes[-1] + 1}"

    @property
    def document_count(self) -> int:
        return len([d for d in self.documents if not d.excluded])

    # -- confidence --------------------------------------------------------
    @property
    def association_confidence(self) -> float:
        """The packet is only as trustworthy as its least certain document."""
        confidences = [
            document.association_confidence for document in self.documents if not document.excluded
        ]
        if not confidences:
            return 0.0
        return round(min(confidences), 4)

    @property
    def requires_review(self) -> bool:
        if self.is_unknown:
            return bool(self.documents)
        return bool(self.review_reasons) or any(
            document.association_review for document in self.documents if not document.excluded
        )

    # -- membership --------------------------------------------------------
    def add(self, document: DocumentGroup) -> None:
        if document not in self.documents:
            self.documents.append(document)
        document.packet_id = self.id
        self._sort()

    def remove(self, document: DocumentGroup) -> None:
        if document in self.documents:
            self.documents.remove(document)
        if document.packet_id == self.id:
            document.packet_id = None

    def _sort(self) -> None:
        self.documents.sort(key=lambda d: d.start_page_index)

    def add_review_reason(self, reason: str) -> None:
        if reason and reason not in self.review_reasons:
            self.review_reasons.append(reason)

    def clear_review(self) -> None:
        self.review_reasons.clear()

    def ordered_documents(self, type_order: tuple[str, ...]) -> list[DocumentGroup]:
        """Documents in the configured packet order for a combined PDF.

        Types are emitted in ``type_order``; anything not listed follows in
        source order. Documents sharing a type keep their source order, so a
        candidate with two resumes keeps them the way the source PDF had them.
        """
        rank = {document_type: index for index, document_type in enumerate(type_order)}
        fallback = len(rank)
        return sorted(
            (d for d in self.documents if not d.excluded and d.export_page_indexes),
            key=lambda d: (rank.get(d.document_type, fallback), d.start_page_index),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_pdf": self.source_pdf,
            "candidate": self.candidate.to_dict(),
            "display_name": self.display_name,
            "is_unknown": self.is_unknown,
            "document_ids": [d.id for d in self.documents],
            "page_indexes": self.page_indexes,
            "association_confidence": round(self.association_confidence, 4),
            "boundary_confidence": round(self.boundary_confidence, 4),
            "boundary_reasons": list(self.boundary_reasons),
            "review_reasons": list(self.review_reasons),
        }


__all__ = ["CandidatePacket", "UNKNOWN_PACKET_ID"]
