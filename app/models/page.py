"""Per-page analysis model.

A :class:`PageAnalysis` carries the two *independent* answers the pipeline must
produce for every page (see the architectural rule in the specification):

* **What kind of document is this page part of?** -> :attr:`predicted_type`
* **Does this page start a new logical document?** -> :attr:`starts_new_document`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .candidate import Candidate
from .enums import ClassificationSource, SeparatorState, TextSource


@dataclass
class PageAnalysis:
    """Everything the pipeline knows about a single PDF page."""

    source_pdf: str
    page_index: int  # zero-based
    page_count: int = 0

    # --- text -------------------------------------------------------------
    extracted_text: str = ""
    text_source: TextSource = TextSource.NONE
    char_count: int = 0

    # --- question A: classification --------------------------------------
    predicted_type: str = "Other"
    classification_confidence: float = 0.0
    classification_source: ClassificationSource = ClassificationSource.RULES
    type_scores: dict[str, float] = field(default_factory=dict)

    # --- question B: document boundary -----------------------------------
    starts_new_document: bool = True
    boundary_confidence: float = 0.0
    boundary_reasons: list[str] = field(default_factory=list)

    # --- identity ---------------------------------------------------------
    candidate: Candidate = field(default_factory=Candidate)

    # --- review state -----------------------------------------------------
    requires_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    excluded: bool = False
    separator_state: SeparatorState = SeparatorState.NOT_SEPARATOR
    separator_label: str | None = None

    # --- diagnostics ------------------------------------------------------
    ocr_used: bool = False
    ocr_failed: bool = False
    ai_used: bool = False
    reasoning_summary: str | None = None
    error: str | None = None

    @property
    def page_number(self) -> int:
        """One-based page number for display."""
        return self.page_index + 1

    @property
    def is_separator(self) -> bool:
        return self.separator_state is not SeparatorState.NOT_SEPARATOR

    @property
    def is_excluded_separator(self) -> bool:
        return self.separator_state is SeparatorState.EXCLUDED

    def add_review_reason(self, reason: str) -> None:
        if reason and reason not in self.review_reasons:
            self.review_reasons.append(reason)
        self.requires_review = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_pdf": self.source_pdf,
            "page_index": self.page_index,
            "page_count": self.page_count,
            "text_source": self.text_source.value,
            "char_count": self.char_count,
            "predicted_type": self.predicted_type,
            "classification_confidence": round(self.classification_confidence, 4),
            "classification_source": self.classification_source.value,
            "starts_new_document": self.starts_new_document,
            "boundary_confidence": round(self.boundary_confidence, 4),
            "boundary_reasons": list(self.boundary_reasons),
            "candidate": self.candidate.to_dict(),
            "requires_review": self.requires_review,
            "review_reasons": list(self.review_reasons),
            "excluded": self.excluded,
            "separator_state": self.separator_state.value,
            "separator_label": self.separator_label,
            "ocr_used": self.ocr_used,
            "ai_used": self.ai_used,
            "error": self.error,
        }


__all__ = ["PageAnalysis"]
