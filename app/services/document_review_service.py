"""Document-level AI review for the few segments that stay uncertain.

The page-level escalation path asks the provider about one page at a time.
On a long file that is both expensive and badly framed: a hundred pages means
up to a hundred requests, and each one asks "what is this page?" when the
question that actually matters is "where does this document start and stop,
and whose is it?"

This asks the better question, and asks it far less often. After grouping, a
handful of *logical documents* may still be uncertain; each gets one request
carrying bounded context -- its opening, its ending, a sample from the middle,
its page range, its neighbours' edges and the candidates known in the file --
and the provider answers about the document as a whole, in structured JSON.

Three rules keep it honest:

*Deterministic structure always wins.* A document a structured parser produced
is never sent. The file stated its own shape; a probabilistic second opinion
can only make that worse.

*A refusal is an answer.* If the provider replies with something unusable, the
document keeps what it had and stays flagged. Nothing is overwritten on the
strength of a malformed reply.

*Bounded cost.* At most :data:`MAX_DOCUMENT_REQUESTS` requests per file, so a
pathological file cannot turn into a hundred calls by another route.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.intelligence.base import DocumentIntelligenceProvider
from app.models.document import DocumentGroup
from app.models.enums import ClassificationSource
from app.models.source_file import SourceFileAnalysis
from app.profiles.base import OTHER, DocumentProfile
from app.services.confidence import ConfidenceThresholds
from app.utils.logging_setup import get_logger, log_event

logger = get_logger("document.ai")

#: Never more than this many document-level requests for one file. Three
#: uncertain segments is a normal worst case; a file wanting more than this is
#: one the AI is unlikely to rescue anyway.
MAX_DOCUMENT_REQUESTS = 8

#: How much of a document's text to send. Enough to recognise what it is,
#: nowhere near the whole thing -- this text may leave the machine.
_OPENING_CHARS = 1200
_CLOSING_CHARS = 600
_MIDDLE_CHARS = 400
_NEIGHBOUR_CHARS = 300


@dataclass
class DocumentContext:
    """Everything the provider is told about one uncertain document."""

    source_pdf: str
    document_type: str
    first_page: int
    last_page: int
    page_count: int
    opening_text: str = ""
    middle_text: str = ""
    closing_text: str = ""
    previous_closing: str = ""
    next_opening: str = ""
    candidate_name: str = ""
    known_candidates: tuple[str, ...] = ()
    document_types: tuple[str, ...] = ()
    parser_name: str = ""

    def to_prompt(self) -> str:
        """The question, as text a provider can answer."""
        parts = [
            "You are checking one logical document extracted from a larger PDF.",
            "",
            f"Pages {self.first_page}-{self.last_page} of {self.source_pdf}",
            f"Currently classified as: {self.document_type}",
            f"Currently attributed to: {self.candidate_name or 'nobody'}",
            "",
            f"Allowed document types: {', '.join(self.document_types)}",
        ]
        if self.known_candidates:
            parts.append(f"Candidates known in this file: {', '.join(self.known_candidates)}")
        parts += [
            "",
            "--- text ending the previous document ---",
            self.previous_closing or "(nothing before it)",
            "",
            "--- this document opens ---",
            self.opening_text,
        ]
        if self.middle_text:
            parts += ["", "--- from the middle ---", self.middle_text]
        parts += [
            "",
            "--- this document ends ---",
            self.closing_text,
            "",
            "--- text opening the next document ---",
            self.next_opening or "(nothing after it)",
            "",
            "Answer with JSON only:",
            '{"document_type": "<one of the allowed types>",',
            ' "candidate": "<name or empty>",',
            ' "starts_correctly": true|false,',
            ' "ends_correctly": true|false,',
            ' "confidence": 0.0-1.0,',
            ' "reasoning": "<one sentence>"}',
        ]
        return "\n".join(parts)


@dataclass
class DocumentVerdict:
    """The provider's answer, once validated."""

    document_type: str = ""
    candidate: str = ""
    starts_correctly: bool = True
    ends_correctly: bool = True
    confidence: float = 0.0
    reasoning: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return bool(self.document_type) and self.confidence > 0.0


class DocumentReviewService:
    """Second-opinion pass over uncertain logical documents."""

    def __init__(
        self,
        profile: DocumentProfile,
        thresholds: ConfidenceThresholds,
        provider: DocumentIntelligenceProvider | None = None,
        *,
        max_requests: int = MAX_DOCUMENT_REQUESTS,
    ) -> None:
        self.profile = profile
        self.thresholds = thresholds
        self.provider = provider
        self.max_requests = max_requests
        self.requests_made = 0

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def uncertain_documents(self, analysis: SourceFileAnalysis) -> list[DocumentGroup]:
        """Which documents are worth a second opinion.

        Deliberately narrow. A document a structured parser produced is never
        included however it scores: the file stated its shape, and asking a
        model to second-guess that trades certainty for noise.
        """
        uncertain: list[DocumentGroup] = []
        for group in analysis.groups:
            if group.excluded or not group.export_page_indexes:
                continue
            if group.type_manually_set:
                continue
            if group.classification_source is ClassificationSource.DETERMINISTIC:
                continue
            if group.document_type == OTHER or self.thresholds.requires_review(
                group.overall_confidence
            ):
                uncertain.append(group)
        return uncertain

    # ------------------------------------------------------------------
    def review(self, analysis: SourceFileAnalysis) -> int:
        """Ask about each uncertain document. Returns how many were changed."""
        if not self.enabled:
            return 0

        candidates = self.uncertain_documents(analysis)[: self.max_requests]
        if not candidates:
            return 0

        changed = 0
        for group in candidates:
            context = self.build_context(analysis, group)
            verdict = self.ask(context)
            if verdict is None or not verdict.usable:
                continue
            if self._apply(group, verdict):
                changed += 1

        log_event(
            logger,
            "document.ai.reviewed",
            file=analysis.name,
            considered=len(candidates),
            requests=self.requests_made,
            changed=changed,
        )
        return changed

    # ------------------------------------------------------------------
    def build_context(
        self, analysis: SourceFileAnalysis, group: DocumentGroup
    ) -> DocumentContext:
        """Assemble bounded context for one document."""
        indexes = group.export_page_indexes or group.page_indexes
        texts = [
            (analysis.page(index).extracted_text if analysis.page(index) else "")
            for index in indexes
        ]
        body = "\n".join(text for text in texts if text)

        position = analysis.groups.index(group)
        previous = analysis.groups[position - 1] if position > 0 else None
        following = (
            analysis.groups[position + 1] if position + 1 < len(analysis.groups) else None
        )

        packet = analysis.packet_for_document(group)
        known = tuple(
            sorted(
                {
                    p.candidate.name
                    for p in analysis.packets
                    if not p.is_unknown and p.candidate.name
                }
            )
        )

        return DocumentContext(
            source_pdf=analysis.name,
            document_type=group.document_type,
            first_page=group.start_page,
            last_page=group.end_page,
            page_count=group.page_count,
            opening_text=body[:_OPENING_CHARS],
            middle_text=_middle_of(body),
            closing_text=body[-_CLOSING_CHARS:] if len(body) > _CLOSING_CHARS else "",
            previous_closing=_tail_of(analysis, previous),
            next_opening=_head_of(analysis, following),
            candidate_name=(
                packet.candidate.name
                if packet is not None and packet.candidate.name
                else (group.candidate.name or "")
            ),
            known_candidates=known,
            document_types=tuple(self.profile.document_types),
            parser_name=analysis.parser_name,
        )

    def ask(self, context: DocumentContext) -> DocumentVerdict | None:
        """One request. Returns ``None`` when the provider cannot be trusted."""
        if self.provider is None:
            return None
        if self.requests_made >= self.max_requests:
            return None

        self.requests_made += 1
        try:
            reply = self.provider.complete(context.to_prompt())  # type: ignore[attr-defined]
        except AttributeError:
            # A provider without a free-form entry point cannot answer a
            # document-level question; the page-level path still works.
            logger.debug("Provider %s has no document-level entry point", self.provider.name)
            return None
        except Exception as exc:  # a provider must never break analysis
            logger.warning("Document-level AI request failed: %s", exc)
            return None

        return self.parse_verdict(reply)

    def parse_verdict(self, reply: str | dict | None) -> DocumentVerdict | None:
        """Validate a provider's reply. Anything unusable becomes ``None``.

        A malformed answer is treated as no answer: the document keeps what it
        had and stays flagged, which is strictly better than acting on a reply
        nobody can interpret.
        """
        if reply is None:
            return None

        data: Any = reply
        if isinstance(reply, str):
            text = reply.strip()
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                data = json.loads(text[start : end + 1])
            except (ValueError, TypeError):
                return None

        if not isinstance(data, dict):
            return None

        document_type = self.profile.normalize_type(str(data.get("document_type") or ""))
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        return DocumentVerdict(
            document_type=document_type,
            candidate=str(data.get("candidate") or "").strip(),
            starts_correctly=bool(data.get("starts_correctly", True)),
            ends_correctly=bool(data.get("ends_correctly", True)),
            confidence=max(0.0, min(1.0, confidence)),
            reasoning=str(data.get("reasoning") or "").strip(),
            raw=data,
        )

    # ------------------------------------------------------------------
    def _apply(self, group: DocumentGroup, verdict: DocumentVerdict) -> bool:
        """Record the verdict without ever hiding a disagreement."""
        changed = False

        if verdict.document_type and verdict.document_type != group.document_type:
            if verdict.confidence >= self.thresholds.high:
                group.document_type = verdict.document_type
                group.classification_source = ClassificationSource.AI_ASSISTED
                group.classification_confidence = verdict.confidence
                changed = True
            else:
                group.add_review_reason(
                    f"AI suggested this is a {verdict.document_type.lower()} "
                    f"({verdict.confidence * 100:.0f}% sure)"
                )
                changed = True
        elif verdict.document_type == group.document_type and verdict.confidence >= self.thresholds.high:
            # Agreement from an independent look is real evidence.
            group.classification_confidence = max(
                group.classification_confidence, verdict.confidence
            )
            group.classification_source = ClassificationSource.AI_ASSISTED
            changed = True

        if not verdict.starts_correctly:
            group.add_review_reason("AI thinks this document starts on the wrong page")
            changed = True
        if not verdict.ends_correctly:
            group.add_review_reason("AI thinks this document ends on the wrong page")
            changed = True

        return changed


# ----------------------------------------------------------------------
def _middle_of(body: str) -> str:
    if len(body) <= _OPENING_CHARS + _CLOSING_CHARS + _MIDDLE_CHARS:
        return ""
    midpoint = len(body) // 2
    half = _MIDDLE_CHARS // 2
    return body[midpoint - half : midpoint + half]


def _tail_of(analysis: SourceFileAnalysis, group: DocumentGroup | None) -> str:
    if group is None or not group.page_indexes:
        return ""
    page = analysis.page(group.page_indexes[-1])
    return (page.extracted_text or "")[-_NEIGHBOUR_CHARS:] if page else ""


def _head_of(analysis: SourceFileAnalysis, group: DocumentGroup | None) -> str:
    if group is None or not group.page_indexes:
        return ""
    page = analysis.page(group.page_indexes[0])
    return (page.extracted_text or "")[:_NEIGHBOUR_CHARS] if page else ""


__all__ = [
    "DocumentReviewService",
    "DocumentContext",
    "DocumentVerdict",
    "MAX_DOCUMENT_REQUESTS",
]
