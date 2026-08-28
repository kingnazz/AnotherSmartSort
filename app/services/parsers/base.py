"""The structured-parser contract.

A *structured parser* recognises a specific, machine-generated document
format -- an applicant tracking system's export, a bulk compile, a submitted
application packet -- and extracts it deterministically, from the format's own
structure rather than by scoring how each page looks.

Every parser answers two questions, deliberately kept apart:

``can_parse``
    Does this file match my format, and how sure am I? Cheap, read-only, and
    run against every registered parser so the registry can pick the strongest
    claim. It must never mutate anything.

``parse``
    Assign type, boundary and identity to every page. Only ever called on the
    one parser that won, so a file is never parsed twice or half-parsed by a
    loser.

Keeping the two apart is what lets the registry choose safely: a parser that
would make a mess of a file it half-recognises still gets to say "0.2, not
mine" without having touched it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.models.enums import ClassificationSource, SeparatorPolicy
from app.models.page import PageAnalysis
from app.services.text_features import PageFeatures

#: A deterministic assignment is exact by construction for a file matching the
#: format, not a probabilistic estimate. Kept just under 1.0 so it stays
#: distinguishable from a human's manual confirmation.
DETERMINISTIC_CONFIDENCE = 0.99

#: Minimum ``can_parse`` confidence for the registry to hand a file over. Below
#: this the file falls through to the generic pipeline, which is always the
#: safer outcome than forcing a parser onto a format it only half-recognises.
MIN_MATCH_CONFIDENCE = 0.60


@dataclass(frozen=True)
class ParserMatch:
    """How strongly a parser claims a file, and why."""

    confidence: float = 0.0
    reason: str = ""

    @property
    def matched(self) -> bool:
        return self.confidence >= MIN_MATCH_CONFIDENCE

    @classmethod
    def no(cls) -> "ParserMatch":
        return cls(0.0, "")


@dataclass
class ParseOutcome:
    """What a parser did, for diagnostics and review messaging.

    ``warnings`` are surfaced to the user rather than swallowed: a parser that
    recognised a format but found it internally inconsistent (a roster
    promising 14 applicants when only 13 were found) must say so, because a
    silent partial success is the failure mode that loses documents.
    """

    parser: str = ""
    structure_confidence: float = DETERMINISTIC_CONFIDENCE
    documents_found: int = 0
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def warn(self, message: str) -> None:
        if message and message not in self.warnings:
            self.warnings.append(message)


@runtime_checkable
class StructuredParser(Protocol):
    """A parser for one recognised, machine-generated document format."""

    #: Stable identifier used in logs, diagnostics and review messages.
    name: str

    def can_parse(self, features_list: list[PageFeatures]) -> ParserMatch:
        """How strongly this file matches. Read-only; never mutates."""
        ...

    def parse(
        self,
        pages: list[PageAnalysis],
        features_list: list[PageFeatures],
        *,
        separator_policy: SeparatorPolicy,
    ) -> ParseOutcome:
        """Assign type, boundary and identity to every page, in place."""
        ...


def assign_page(
    page: PageAnalysis,
    document_type: str,
    candidate,
    *,
    starts_new_document: bool,
    parser_name: str,
    reason: str = "",
    confidence: float = DETERMINISTIC_CONFIDENCE,
) -> None:
    """Record one deterministic page decision.

    Shared by every parser so a page carries the same shape of evidence
    whichever format it came from, and so ``ClassificationSource`` is set in
    exactly one place.

    Confidence is deliberately *not* reduced for an interior page that looks
    unremarkable on its own. Once the file's structure has established which
    document a page belongs to, the page's own appearance is weaker evidence
    than that structure, and letting a bland middle page drag the group's
    confidence down is what used to send correct documents to review.
    """
    detail = reason or (
        "section starts here" if starts_new_document else "continues open section"
    )
    page.predicted_type = document_type
    page.classification_confidence = confidence
    page.classification_source = ClassificationSource.DETERMINISTIC
    page.starts_new_document = starts_new_document
    page.boundary_confidence = confidence
    page.boundary_reasons = [f"{parser_name}: {detail}"]
    page.reasoning_summary = f"{parser_name}: {document_type}"
    page.candidate = candidate


__all__ = [
    "StructuredParser",
    "ParserMatch",
    "ParseOutcome",
    "assign_page",
    "DETERMINISTIC_CONFIDENCE",
    "MIN_MATCH_CONFIDENCE",
]
