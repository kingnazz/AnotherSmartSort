"""Composition root: builds a configured pipeline from :class:`AppSettings`.

Keeping assembly in one place means the UI never has to know how the pieces fit
together, and tests can build the same object graph in a single call.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.intelligence import build_ai_provider
from app.intelligence.base import DocumentIntelligenceProvider, ProviderAvailability
from app.intelligence.rules_provider import RulesProvider
from app.profiles import get_profile
from app.profiles.base import DocumentProfile
from app.services.classification_service import ClassificationService
from app.services.confidence import ConfidenceThresholds
from app.services.export_service import ExportService
from app.services.grouping_service import GroupingService
from app.services.ocr_service import OCRAvailability, OCRService, build_ocr_service
from app.services.packet_service import CandidatePacketService
from app.services.parsers.registry import build_default_registry
from app.services.processing_service import ProcessingPipeline
from app.storage.settings_store import AppSettings, SettingsStore
from app.utils.logging_setup import get_logger

logger = get_logger("services")


@dataclass
class AnalysisServices:
    """A fully wired pipeline plus the pieces the UI needs to talk about it."""

    profile: DocumentProfile
    pipeline: ProcessingPipeline
    classification: ClassificationService
    grouping: GroupingService
    packets: CandidatePacketService
    ocr: OCRService
    thresholds: ConfidenceThresholds
    ai_provider: DocumentIntelligenceProvider | None = None

    @property
    def provider_name(self) -> str:
        return self.ai_provider.name if self.ai_provider else "Rules Only"

    @property
    def sends_data_externally(self) -> bool:
        return bool(self.ai_provider and self.ai_provider.sends_data_externally)

    def provider_availability(self) -> ProviderAvailability:
        if self.ai_provider is None:
            return ProviderAvailability(
                True, "Rules Only - everything stays on this computer."
            )
        return self.ai_provider.is_available()

    def ocr_availability(self) -> OCRAvailability:
        return self.ocr.availability()

    def close(self) -> None:
        if self.ai_provider is not None:
            self.ai_provider.close()


def build_analysis_services(
    settings: AppSettings, *, settings_store: SettingsStore | None = None
) -> AnalysisServices:
    """Construct every service the analysis pipeline needs from settings."""
    profile = get_profile(settings.profile_name)
    thresholds = settings.thresholds

    api_key = ""
    if settings.provider_kind.is_external:
        store = settings_store or SettingsStore()
        api_key = store.get_openai_key()

    ai_provider = build_ai_provider(
        settings.provider_kind,
        profile,
        openai_api_key=api_key,
        openai_model=settings.openai_model,
        openai_timeout=settings.openai_timeout_seconds,
        ollama_url=settings.ollama_url,
        ollama_model=settings.ollama_model,
        ollama_timeout=settings.ollama_timeout_seconds,
    )

    classification = ClassificationService(
        profile,
        RulesProvider(profile),
        ai_provider,
        escalation_threshold=settings.ai_escalation_threshold,
    )
    ocr = build_ocr_service(
        enabled=settings.ocr_enabled,
        executable=settings.tesseract_path,
        language=settings.ocr_language,
    )
    grouping = GroupingService(profile, thresholds, settings.separator_policy_enum)
    packets = CandidatePacketService(profile, thresholds)
    # Tier A of the classification priority order: files matching a known
    # format (PageUp bulk compile, submitted applicant packet, separator-page
    # ATS export) are parsed deterministically, ahead of the rules classifier
    # and any AI escalation. Anything unrecognised falls through untouched.
    parser_registry = build_default_registry(profile)
    pipeline = ProcessingPipeline(
        profile,
        classification,
        ocr,
        grouping,
        thresholds=thresholds,
        separator_policy=settings.separator_policy_enum,
        packet_service=packets,
        parser_registry=parser_registry,
    )

    return AnalysisServices(
        profile=profile,
        pipeline=pipeline,
        classification=classification,
        grouping=grouping,
        packets=packets,
        ocr=ocr,
        thresholds=thresholds,
        ai_provider=ai_provider,
    )


def build_export_service(settings: AppSettings) -> ExportService:
    """Construct the export service from settings."""
    profile = get_profile(settings.profile_name)
    return ExportService(
        filename_template=settings.filename_template,
        folder_per_candidate=settings.folder_per_candidate,
        group_by_document_type=settings.group_by_document_type,
        export_separate_documents=settings.export_separate_documents,
        export_combined_packets=settings.export_combined_packets,
        packet_order=profile.packet_order,
        document_types=settings.export_document_types,
        # Sort & Save keeps each run in its own timestamped folder, so two
        # runs into the same chosen directory stay separable afterwards.
        batch_folder=True,
    )


__all__ = ["AnalysisServices", "build_analysis_services", "build_export_service"]
