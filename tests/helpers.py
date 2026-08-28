"""Shared test helpers for building pipelines and page text."""

from __future__ import annotations

from app.intelligence.base import DocumentIntelligenceProvider
from app.intelligence.rules_provider import RulesProvider
from app.models.enums import SeparatorPolicy
from app.profiles.base import DocumentProfile
from app.services.ats_parser import AtsReportParser
from app.services.classification_service import ClassificationService
from app.services.parsers.registry import ATSParserRegistry
from app.services.confidence import ConfidenceThresholds
from app.services.grouping_service import GroupingService
from app.services.ocr_service import NullOCRProvider, OCRService
from app.services.processing_service import ProcessingPipeline


def build_pipeline(
    profile: DocumentProfile,
    thresholds: ConfidenceThresholds,
    *,
    ai_provider: DocumentIntelligenceProvider | None = None,
    ocr: OCRService | None = None,
    separator_policy: SeparatorPolicy | None = None,
    escalation_threshold: float = 0.85,
    ats_parser: AtsReportParser | None = None,
    parser_registry: ATSParserRegistry | None = None,
) -> ProcessingPipeline:
    """Construct a pipeline for tests, with optional AI/OCR substitutes.

    Both structured-parsing arguments default to ``None`` so most tests keep
    exercising the generic rules/AI pipeline unchanged. Pass
    ``parser_registry=build_default_registry(profile)`` to exercise every
    recognised format, or ``ats_parser=AtsReportParser(profile)`` for just the
    separator-page one.
    """
    policy = separator_policy or SeparatorPolicy.INCLUDE
    classification = ClassificationService(
        profile,
        RulesProvider(profile),
        ai_provider,
        escalation_threshold=escalation_threshold,
    )
    return ProcessingPipeline(
        profile,
        classification,
        ocr or OCRService(NullOCRProvider(), enabled=False),
        GroupingService(profile, thresholds, policy),
        thresholds=thresholds,
        separator_policy=policy,
        ats_parser=ats_parser,
        parser_registry=parser_registry,
    )


def group_shape(analysis) -> list[tuple[str, int, int]]:
    """``[(document_type, first_page, last_page), ...]`` for readable assertions."""
    return [(g.document_type, g.start_page, g.end_page) for g in analysis.groups]


def page_text(*blocks: list[str]) -> str:
    """Join sample-data line blocks into one page of text."""
    lines: list[str] = []
    for block in blocks:
        lines.extend(block)
    return "\n".join(lines)


__all__ = ["build_pipeline", "group_shape", "page_text"]
