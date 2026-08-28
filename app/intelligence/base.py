"""Document-intelligence provider interface.

Everything above this layer -- the pipeline, grouping, the UI -- talks only to
:class:`DocumentIntelligenceProvider`. Rules-only, OpenAI and Ollama are
interchangeable implementations, so no provider-specific behaviour leaks into
the rest of the application.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.models.candidate import Candidate
from app.models.enums import ProviderKind
from app.services.text_features import PageFeatures


@dataclass(frozen=True)
class ProviderAvailability:
    """Whether a provider can be used right now, and why not if it cannot."""

    available: bool
    message: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.available


@dataclass
class PageContext:
    """Bounded context handed to a provider for one page.

    Deliberately small: the current page's text plus a little surrounding
    context. Whole PDFs are never sent to an external provider.
    """

    source_pdf: str
    page_index: int
    page_count: int
    text: str
    features: PageFeatures | None = None

    previous_type: str | None = None
    previous_confidence: float = 0.0
    previous_text_tail: str = ""
    next_text_head: str = ""
    previous_features: PageFeatures | None = None
    previous_group_type: str | None = None
    previous_group_page_count: int = 0
    #: Document type announced by a separator page immediately before this one.
    previous_separator_type: str | None = None

    candidate: Candidate = field(default_factory=Candidate)
    previous_candidate: Candidate = field(default_factory=Candidate)
    document_types: tuple[str, ...] = ()
    profile_name: str = ""

    @property
    def page_number(self) -> int:
        return self.page_index + 1

    @property
    def is_first_page(self) -> bool:
        return self.page_index == 0


@dataclass
class PageClassification:
    """Answer to *what kind of document is this page part of?*"""

    document_type: str
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)
    reasoning: str | None = None
    explanation: list[tuple[str, float]] = field(default_factory=list)
    raw: dict[str, Any] | None = None


@dataclass
class BoundaryAssessment:
    """Answer to *does this page start a new logical document?*"""

    starts_new_document: bool
    confidence: float
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0
    raw: dict[str, Any] | None = None


@dataclass
class PageInsight:
    """Combined per-page result, including any identity the provider recovered."""

    classification: PageClassification
    boundary: BoundaryAssessment
    candidate: Candidate = field(default_factory=Candidate)
    reasoning: str | None = None
    used_ai: bool = False
    requests: int = 0
    error: str | None = None


class DocumentIntelligenceProvider(ABC):
    """Common interface implemented by every intelligence provider."""

    kind: ProviderKind = ProviderKind.RULES
    name: str = "provider"
    #: True when using this provider transmits page text off the machine.
    sends_data_externally: bool = False

    @abstractmethod
    def is_available(self) -> ProviderAvailability:
        """Report whether the provider can service requests right now."""

    @abstractmethod
    def classify_page(self, context: PageContext) -> PageClassification:
        """Classify a single page."""

    @abstractmethod
    def analyze_boundary(self, context: PageContext) -> BoundaryAssessment:
        """Decide whether a page begins a new logical document."""

    def analyze_page(self, context: PageContext) -> PageInsight:
        """Answer both questions.

        The default implementation runs the two calls independently; providers
        that can answer both in one round trip (the AI providers) override this.
        """
        classification = self.classify_page(context)
        boundary = self.analyze_boundary(context)
        return PageInsight(
            classification=classification,
            boundary=boundary,
            candidate=Candidate(),
            reasoning=classification.reasoning,
        )

    def close(self) -> None:
        """Release any resources (network sessions, handles). Optional."""


__all__ = [
    "DocumentIntelligenceProvider",
    "ProviderAvailability",
    "PageContext",
    "PageClassification",
    "BoundaryAssessment",
    "PageInsight",
]
