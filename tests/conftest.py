"""Shared fixtures.

Sample PDFs are generated once per test session into a temporary directory --
no binary fixtures and no real applicant documents are ever committed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.intelligence.rules_provider import RulesProvider
from app.models.enums import SeparatorPolicy
from app.profiles import get_profile
from app.profiles.base import DocumentProfile
from app.services.ats_parser import AtsReportParser
from app.services.confidence import ConfidenceThresholds
from app.services.metadata_service import MetadataExtractor
from app.services.processing_service import ProcessingPipeline
from app.services.text_features import extract_features
from scripts import sample_data
from tests.helpers import build_pipeline

# Keep test output readable; the pipeline logs at INFO by design.
logging.getLogger("smartpdfsorter").setLevel(logging.CRITICAL)


@pytest.fixture(scope="session")
def profile() -> DocumentProfile:
    return get_profile()


@pytest.fixture(scope="session")
def thresholds() -> ConfidenceThresholds:
    return ConfidenceThresholds()


@pytest.fixture(scope="session")
def samples_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Every synthetic sample PDF, built once for the whole session."""
    directory = tmp_path_factory.mktemp("samples")
    sample_data.build_all(directory)
    return directory


@pytest.fixture(scope="session")
def ats_samples_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Every synthetic ATS-export-structure PDF, built once for the whole session.

    The four named, real-file-derived fixtures plus the ~80-page
    multi-applicant batch.
    """
    from scripts import ats_fixtures

    directory = tmp_path_factory.mktemp("ats_samples")
    for factory in ats_fixtures.NAMED_BATCHES:
        factory().write(directory)
    ats_fixtures.build_multi_applicant_batch().write(directory)
    return directory


@pytest.fixture
def rules(profile: DocumentProfile) -> RulesProvider:
    return RulesProvider(profile)


@pytest.fixture
def metadata() -> MetadataExtractor:
    return MetadataExtractor()


@pytest.fixture
def pipeline(profile: DocumentProfile, thresholds: ConfidenceThresholds) -> ProcessingPipeline:
    """A Rules-Only pipeline with OCR disabled."""
    return build_pipeline(profile, thresholds)


@pytest.fixture(scope="session")
def pipeline_factory(profile: DocumentProfile, thresholds: ConfidenceThresholds):
    """Build a pipeline from a module- or session-scoped fixture.

    ``pipeline`` is function-scoped and cannot be requested by a longer-lived
    fixture; analysing an 85-page batch once per test would dominate the run.
    """

    def _build() -> ProcessingPipeline:
        return build_pipeline(profile, thresholds)

    return _build


@pytest.fixture
def ats_pipeline(profile: DocumentProfile, thresholds: ConfidenceThresholds) -> ProcessingPipeline:
    """A pipeline with the deterministic ATS report parser (Tier A) wired in.

    Separator pages default to excluded, matching the shipped application's
    default so fixture page ranges match what a real export produces.
    """
    return build_pipeline(
        profile,
        thresholds,
        separator_policy=SeparatorPolicy.EXCLUDE,
        ats_parser=AtsReportParser(profile),
    )


@pytest.fixture
def grouping(profile: DocumentProfile, thresholds: ConfidenceThresholds):
    """The grouping service, for testing corrections without the UI."""
    from app.services.grouping_service import GroupingService

    return GroupingService(profile, thresholds, SeparatorPolicy.EXCLUDE)


@pytest.fixture
def packets(profile: DocumentProfile, thresholds: ConfidenceThresholds):
    """The candidate packet service, for testing attribution corrections."""
    from app.services.packet_service import CandidatePacketService

    return CandidatePacketService(profile, thresholds)


@pytest.fixture
def features_of():
    """Helper: build :class:`PageFeatures` from a list of lines or raw text."""

    def _build(text: str | list[str]):
        if isinstance(text, list):
            text = "\n".join(text)
        return extract_features(text)

    return _build
