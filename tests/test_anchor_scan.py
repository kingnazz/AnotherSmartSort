"""Whole-file anchor segmentation, and the document-level AI fallback.

Both exist for the same reason: on a long file, a page's own appearance is
weak evidence compared to what the file's structure says. The anchor pass
finds that structure cheaply; the AI pass is a last resort for the few
segments still uncertain, asked once per document rather than once per page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import ClassificationSource
from app.profiles.recruiting import COVER_LETTER, RESUME
from app.services.anchor_scan import ANCHOR_THRESHOLD, AnchorScanner
from app.services.confidence import ConfidenceThresholds
from app.services.document_review_service import (
    DocumentReviewService,
    DocumentVerdict,
)
from app.services.text_features import extract_features
from scripts import sample_data


def features_for(document) -> list:
    return [extract_features("\n".join(page.lines)) for page in document.pages]


class TestAnchorScoring:
    def test_the_first_page_always_opens_a_document(self, profile) -> None:
        scan = AnchorScanner(profile).scan(features_for(sample_data.sample_b()))
        assert scan.pages[0].opens_document

    def test_a_separator_page_opens_a_document(self, profile) -> None:
        scan = AnchorScanner(profile).scan(features_for(sample_data.sample_f()))
        # sample_f opens with a "Resume" separator page.
        assert scan.pages[0].opens_document

    def test_the_page_after_a_separator_continues_it(self, profile) -> None:
        """A separator announces what follows; the next page is that document,
        not a second one."""
        scan = AnchorScanner(profile).scan(features_for(sample_data.sample_f()))
        assert not scan.pages[1].opens_document

    def test_a_continuation_page_does_not_open_a_document(self, profile) -> None:
        """Page 2 of 3 is explicitly not the start of anything."""
        scan = AnchorScanner(profile).scan(features_for(sample_data.sample_b()))
        assert not scan.pages[1].opens_document
        assert scan.pages[1].score < ANCHOR_THRESHOLD

    def test_a_three_page_resume_is_one_segment(self, profile) -> None:
        scan = AnchorScanner(profile).scan(features_for(sample_data.sample_b()))
        assert len(scan.segments) == 1
        assert scan.segments[0].page_count == 3

    def test_a_multi_document_packet_segments(self, profile) -> None:
        scan = AnchorScanner(profile).scan(features_for(sample_data.sample_a()))
        assert len(scan.segments) >= 3

    def test_every_page_belongs_to_exactly_one_segment(self, profile) -> None:
        document = sample_data.sample_a()
        scan = AnchorScanner(profile).scan(features_for(document))

        covered = [index for segment in scan.segments for index in segment.page_indexes]
        assert covered == sorted(covered)
        assert covered == list(range(len(document.pages)))

    def test_anchors_explain_themselves(self, profile) -> None:
        """A boundary the user cannot understand is one they cannot trust."""
        scan = AnchorScanner(profile).scan(features_for(sample_data.sample_f()))
        assert scan.pages[0].reasons
        assert all(isinstance(reason, str) for reason in scan.pages[0].reasons)


class TestAnchorsHelpTheGenericPipeline:
    def test_a_long_resume_still_stays_one_document(
        self, pipeline, samples_dir: Path
    ) -> None:
        """The pass must not fragment what already worked."""
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_b().filename)
        assert [(g.document_type, g.start_page, g.end_page) for g in analysis.groups] == [
            (RESUME, 1, 3)
        ]

    def test_a_full_packet_still_splits_correctly(
        self, pipeline, samples_dir: Path
    ) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)
        assert [g.document_type for g in analysis.groups] == [
            "Application Report",
            RESUME,
            COVER_LETTER,
            "References",
        ]

    def test_separator_documents_still_work(self, pipeline, samples_dir: Path) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_f().filename)
        assert [g.document_type for g in analysis.groups] == [RESUME, COVER_LETTER]

    def test_structure_confidence_is_recorded(self, pipeline, samples_dir: Path) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)
        assert analysis.structure_confidence > 0.0


class _StubProvider:
    """A provider that answers document-level questions from a script."""

    name = "stub"
    sends_data_externally = False

    def __init__(self, replies: list[str | None]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else None

    def is_available(self):  # pragma: no cover - unused here
        from app.intelligence.base import ProviderAvailability

        return ProviderAvailability(True, "stub")

    def close(self) -> None:  # pragma: no cover - unused here
        pass


@pytest.fixture
def review_service(profile, thresholds):
    def _build(replies):
        return DocumentReviewService(profile, thresholds, _StubProvider(replies))

    return _build


class TestDocumentLevelAI:
    def test_deterministic_documents_are_never_sent(
        self, profile, thresholds, ats_pipeline, ats_samples_dir: Path
    ) -> None:
        """The file stated its own shape; a second opinion can only hurt."""
        from scripts.ats_fixtures import trevor_hollands_batch

        batch = trevor_hollands_batch()
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)
        service = DocumentReviewService(profile, thresholds, _StubProvider([]))

        assert service.uncertain_documents(analysis) == []
        assert service.review(analysis) == 0

    def test_one_request_per_document_not_per_page(
        self, review_service, pipeline, samples_dir: Path
    ) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_e().filename)
        service = review_service(
            ['{"document_type": "Resume", "confidence": 0.95, '
             '"starts_correctly": true, "ends_correctly": true}']
            * 8
        )
        service.review(analysis)

        uncertain = len(service.uncertain_documents(analysis))
        assert service.requests_made <= max(uncertain, 1)
        assert service.requests_made < analysis.page_count

    def test_requests_are_capped(self, profile, thresholds, pipeline, samples_dir) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)
        for group in analysis.groups:
            group.document_type = "Other"
            group.type_manually_set = False
            group.classification_source = ClassificationSource.RULES

        service = DocumentReviewService(
            profile, thresholds, _StubProvider([None] * 50), max_requests=2
        )
        service.review(analysis)
        assert service.requests_made <= 2

    def test_the_prompt_carries_bounded_context(
        self, profile, thresholds, pipeline, samples_dir: Path
    ) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)
        service = DocumentReviewService(profile, thresholds, _StubProvider([]))
        context = service.build_context(analysis, analysis.groups[1])
        prompt = context.to_prompt()

        assert "Pages" in prompt
        assert "Allowed document types" in prompt
        assert "JSON" in prompt
        # Bounded: never the whole document.
        assert len(prompt) < 6000

    def test_a_confident_disagreement_retypes_the_document(
        self, review_service, pipeline, samples_dir: Path
    ) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)
        group = analysis.groups[1]
        group.document_type = "Other"
        group.classification_source = ClassificationSource.RULES
        group.classification_confidence = 0.4

        service = review_service(
            ['{"document_type": "Resume", "candidate": "Benjamin Perez", '
             '"starts_correctly": true, "ends_correctly": true, "confidence": 0.97}']
        )
        service.review(analysis)
        assert group.document_type == RESUME

    def test_an_unconfident_disagreement_only_flags(
        self, review_service, pipeline, samples_dir: Path
    ) -> None:
        """A weak second opinion must not overwrite the first."""
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)
        group = analysis.groups[1]
        original = group.document_type
        group.document_type = "Other"
        group.classification_source = ClassificationSource.RULES
        group.classification_confidence = 0.4

        service = review_service(
            ['{"document_type": "Cover Letter", "starts_correctly": true, '
             '"ends_correctly": true, "confidence": 0.55}']
        )
        service.review(analysis)

        assert group.document_type == "Other"
        assert group.requires_review
        assert any("AI suggested" in reason for reason in group.review_reasons)

    def test_boundary_doubts_are_surfaced(
        self, review_service, pipeline, samples_dir: Path
    ) -> None:
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)
        group = analysis.groups[1]
        group.document_type = "Other"
        group.classification_source = ClassificationSource.RULES
        group.classification_confidence = 0.4

        service = review_service(
            ['{"document_type": "Resume", "starts_correctly": false, '
             '"ends_correctly": false, "confidence": 0.95}']
        )
        service.review(analysis)

        reasons = " ".join(group.review_reasons)
        assert "starts on the wrong page" in reasons
        assert "ends on the wrong page" in reasons


class TestVerdictParsing:
    @pytest.mark.parametrize(
        "reply",
        [None, "", "not json at all", "{unclosed", "[]", '{"document_type": }'],
    )
    def test_unusable_replies_are_rejected(self, profile, thresholds, reply) -> None:
        """A malformed answer is no answer -- never act on it."""
        service = DocumentReviewService(profile, thresholds, _StubProvider([]))
        verdict = service.parse_verdict(reply)
        assert verdict is None or not verdict.usable

    def test_json_wrapped_in_prose_is_recovered(self, profile, thresholds) -> None:
        """Providers commonly narrate around the JSON they were asked for."""
        service = DocumentReviewService(profile, thresholds, _StubProvider([]))
        verdict = service.parse_verdict(
            'Sure! Here is the answer:\n{"document_type": "Resume", "confidence": 0.9}\nHope that helps.'
        )
        assert verdict is not None
        assert verdict.document_type == RESUME
        assert verdict.confidence == pytest.approx(0.9)

    def test_an_unknown_type_normalises_rather_than_injecting(
        self, profile, thresholds
    ) -> None:
        service = DocumentReviewService(profile, thresholds, _StubProvider([]))
        verdict = service.parse_verdict('{"document_type": "curriculum vitae", "confidence": 0.9}')
        assert verdict is not None
        assert verdict.document_type in profile.document_types

    def test_a_wild_confidence_is_clamped(self, profile, thresholds) -> None:
        service = DocumentReviewService(profile, thresholds, _StubProvider([]))
        verdict = service.parse_verdict('{"document_type": "Resume", "confidence": 47}')
        assert verdict is not None
        assert verdict.confidence == 1.0

    def test_a_provider_that_throws_is_survivable(self, profile, thresholds) -> None:
        class Exploding(_StubProvider):
            def complete(self, prompt: str):
                raise RuntimeError("boom")

        service = DocumentReviewService(profile, thresholds, Exploding([]))
        from app.services.document_review_service import DocumentContext

        assert service.ask(DocumentContext(source_pdf="x", document_type="Other",
                                           first_page=1, last_page=1, page_count=1)) is None

    def test_a_provider_without_a_document_entry_point_is_skipped(
        self, profile, thresholds
    ) -> None:
        class PageOnly:
            name = "page-only"

        from app.services.document_review_service import DocumentContext

        service = DocumentReviewService(profile, thresholds, PageOnly())
        assert service.ask(DocumentContext(source_pdf="x", document_type="Other",
                                           first_page=1, last_page=1, page_count=1)) is None

    def test_disabled_without_a_provider(self, profile, thresholds, pipeline, samples_dir) -> None:
        service = DocumentReviewService(profile, thresholds, None)
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)
        assert not service.enabled
        assert service.review(analysis) == 0
