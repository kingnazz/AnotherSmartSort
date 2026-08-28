"""Logical document group model.

A :class:`DocumentGroup` is the unit the user reviews and the unit exported to
disk. It spans one or more contiguous pages of a single source PDF -- a 3-page
resume is one group, never three.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from .candidate import Candidate
from .enums import ClassificationSource, SeparatorState
from .page import PageAnalysis

_group_counter = itertools.count(1)


def _new_group_id() -> str:
    return f"grp-{next(_group_counter):06d}"


@dataclass
class DocumentGroup:
    """A contiguous run of pages forming one logical document."""

    source_pdf: str
    page_indexes: list[int] = field(default_factory=list)
    document_type: str = "Other"
    candidate: Candidate = field(default_factory=Candidate)
    classification_confidence: float = 0.0
    boundary_confidence: float = 0.0
    id: str = field(default_factory=_new_group_id)

    classification_source: ClassificationSource = ClassificationSource.RULES
    type_manually_set: bool = False
    excluded: bool = False

    requires_review: bool = False
    review_reasons: list[str] = field(default_factory=list)

    #: Which candidate packet this document was attributed to, and how sure we
    #: are. Association is a separate question from "what type is this?" and
    #: carries its own confidence so the two can be reviewed independently.
    packet_id: str | None = None
    association_confidence: float = 0.0
    association_reasons: list[str] = field(default_factory=list)
    association_review: bool = False
    association_manually_set: bool = False

    output_filename: str | None = None
    exported_path: str | None = None
    #: Combined packet PDF this document was written into, when one was created.
    packet_export_path: str | None = None

    #: Page indexes inside this group that are separator pages excluded from output.
    excluded_separator_pages: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.page_indexes = sorted(set(self.page_indexes))

    # -- geometry ---------------------------------------------------------
    @property
    def start_page_index(self) -> int:
        return self.page_indexes[0] if self.page_indexes else -1

    @property
    def end_page_index(self) -> int:
        return self.page_indexes[-1] if self.page_indexes else -1

    @property
    def start_page(self) -> int:
        """One-based first page for display."""
        return self.start_page_index + 1

    @property
    def end_page(self) -> int:
        """One-based last page for display."""
        return self.end_page_index + 1

    @property
    def page_count(self) -> int:
        return len(self.page_indexes)

    @property
    def export_page_indexes(self) -> list[int]:
        """Pages actually written to the output PDF."""
        excluded = set(self.excluded_separator_pages)
        return [i for i in self.page_indexes if i not in excluded]

    @property
    def page_range_label(self) -> str:
        if not self.page_indexes:
            return "No pages"
        if self.page_count == 1:
            return f"Page {self.start_page}"
        return f"Pages {self.start_page}–{self.end_page}"

    @property
    def overall_confidence(self) -> float:
        """Lower of the two confidences: the group is only as sure as its weakest signal."""
        if self.type_manually_set:
            return 1.0
        return min(self.classification_confidence, self.boundary_confidence)

    @property
    def needs_attention(self) -> bool:
        """Review needed for any reason -- type, extent, or who it belongs to."""
        return self.requires_review or self.association_review

    # -- review -----------------------------------------------------------
    def add_review_reason(self, reason: str) -> None:
        if reason and reason not in self.review_reasons:
            self.review_reasons.append(reason)
        self.requires_review = True

    def clear_review(self) -> None:
        self.requires_review = False
        self.review_reasons.clear()

    def set_type(self, document_type: str) -> None:
        """Apply a manual document-type correction."""
        self.document_type = document_type
        self.type_manually_set = True
        self.classification_source = ClassificationSource.MANUAL
        self.classification_confidence = 1.0
        self.clear_review()

    def contains(self, page_index: int) -> bool:
        return page_index in self.page_indexes

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_pdf": self.source_pdf,
            "page_indexes": list(self.page_indexes),
            "document_type": self.document_type,
            "candidate": self.candidate.to_dict(),
            "classification_confidence": round(self.classification_confidence, 4),
            "boundary_confidence": round(self.boundary_confidence, 4),
            "classification_source": self.classification_source.value,
            "type_manually_set": self.type_manually_set,
            "excluded": self.excluded,
            "requires_review": self.requires_review,
            "review_reasons": list(self.review_reasons),
            "output_filename": self.output_filename,
            "exported_path": self.exported_path,
            "excluded_separator_pages": list(self.excluded_separator_pages),
            "packet_id": self.packet_id,
            "association_confidence": round(self.association_confidence, 4),
            "association_reasons": list(self.association_reasons),
            "association_review": self.association_review,
            "packet_export_path": self.packet_export_path,
        }

    def set_association(
        self,
        packet_id: str | None,
        confidence: float,
        reasons: list[str] | None = None,
        *,
        manual: bool = False,
    ) -> None:
        """Record which candidate this document belongs to, and why."""
        self.packet_id = packet_id
        self.association_confidence = round(float(confidence), 4)
        self.association_reasons = list(reasons or [])
        if manual:
            self.association_manually_set = True
            self.association_confidence = 1.0
            self.association_review = False


def apply_separator_policy_to_group(group: DocumentGroup, pages: list[PageAnalysis]) -> None:
    """Sync a group's excluded separator pages with the per-page decisions."""
    excluded = [
        page.page_index
        for page in pages
        if page.page_index in group.page_indexes
        and page.separator_state is SeparatorState.EXCLUDED
    ]
    group.excluded_separator_pages = sorted(excluded)


__all__ = ["DocumentGroup", "apply_separator_policy_to_group"]
