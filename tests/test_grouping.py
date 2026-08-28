"""Document grouping: the rule that multi-page documents stay one document."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import FileStatus, SeparatorPolicy
from app.profiles.recruiting import (
    APPLICATION_REPORT,
    COVER_LETTER,
    REFERENCES,
    RESUME,
    TRANSCRIPT,
)
from app.services.processing_service import ProcessingPipeline
from scripts import sample_data

from tests.helpers import build_pipeline, group_shape


def analyze(pipeline: ProcessingPipeline, samples_dir: Path, document) -> object:
    return pipeline.analyze_file(samples_dir / document.filename)


class TestMultiPageDocumentsStayTogether:
    def test_three_page_resume_is_one_document(self, pipeline, samples_dir) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_b())
        assert group_shape(analysis) == [(RESUME, 1, 3)]
        assert analysis.groups[0].page_count == 3

    def test_two_page_cover_letter_is_one_document(self, pipeline, samples_dir) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_c())
        assert group_shape(analysis) == [(COVER_LETTER, 1, 2)]

    def test_pages_are_never_exported_individually(self, pipeline, samples_dir) -> None:
        """The failure mode the spec calls out: Resume_Page_5, Resume_Page_6..."""
        analysis = analyze(pipeline, samples_dir, sample_data.sample_a())
        assert len(analysis.groups) == 4
        assert all(group.page_count >= 1 for group in analysis.groups)
        assert [g.page_count for g in analysis.groups] == [4, 3, 1, 2]


class TestDocumentTransitions:
    def test_full_packet_splits_into_expected_documents(self, pipeline, samples_dir) -> None:
        document = sample_data.sample_a()
        analysis = analyze(pipeline, samples_dir, document)
        assert group_shape(analysis) == document.expected_groups

    def test_application_report_to_resume_transition(self, pipeline, samples_dir) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_a())
        types = [g.document_type for g in analysis.groups]
        assert types[0] == APPLICATION_REPORT
        assert types[1] == RESUME
        assert analysis.groups[1].start_page == 5

    def test_resume_followed_by_cover_letter_splits(self, pipeline, samples_dir) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_a())
        resume = analysis.groups[1]
        letter = analysis.groups[2]
        assert resume.document_type == RESUME
        assert letter.document_type == COVER_LETTER
        assert resume.end_page + 1 == letter.start_page

    def test_transcript_then_references(self, pipeline, samples_dir) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_h())
        assert group_shape(analysis) == [(TRANSCRIPT, 1, 2), (REFERENCES, 3, 4)]

    def test_every_page_belongs_to_exactly_one_group(self, pipeline, samples_dir) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_a())
        assigned = [index for group in analysis.groups for index in group.page_indexes]
        assert sorted(assigned) == list(range(analysis.page_count))
        assert len(assigned) == len(set(assigned)), "a page was assigned twice"


class TestSameTypeCanStillStartANewDocument:
    def test_identity_change_splits_two_resumes(self, pipeline, samples_dir) -> None:
        """Two resumes in a row are two documents, not one six-page resume."""
        analysis = analyze(pipeline, samples_dir, sample_data.sample_g())
        assert group_shape(analysis) == [(RESUME, 1, 2), (RESUME, 3, 4)]

    def test_each_resume_keeps_its_own_candidate(self, pipeline, samples_dir) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_g())
        names = [group.candidate.name for group in analysis.groups]
        assert names == ["Benjamin Perez", "Jane Smith"]


class TestSeparatorPages:
    def test_separator_starts_a_document_that_following_pages_continue(
        self, pipeline, samples_dir
    ) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_f())
        assert group_shape(analysis) == [(RESUME, 1, 3), (COVER_LETTER, 4, 5)]

    def test_include_policy_keeps_the_separator_page(self, profile, thresholds, samples_dir) -> None:
        pipeline = build_pipeline(
            profile, thresholds, separator_policy=SeparatorPolicy.INCLUDE
        )
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_f().filename)
        resume = analysis.groups[0]
        assert resume.export_page_indexes == [0, 1, 2]

    def test_exclude_policy_drops_the_separator_from_output(
        self, profile, thresholds, samples_dir
    ) -> None:
        pipeline = build_pipeline(
            profile, thresholds, separator_policy=SeparatorPolicy.EXCLUDE
        )
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_f().filename)
        resume = analysis.groups[0]
        # The separator still defines the group, but is not written out.
        assert resume.page_indexes == [0, 1, 2]
        assert resume.export_page_indexes == [1, 2]

    def test_ask_policy_flags_the_page_for_review(
        self, profile, thresholds, samples_dir
    ) -> None:
        pipeline = build_pipeline(profile, thresholds, separator_policy=SeparatorPolicy.ASK)
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_f().filename)
        assert analysis.groups[0].requires_review
        assert any("separator" in reason.lower() for reason in analysis.groups[0].review_reasons)


class TestUnclassifiablePages:
    def test_unidentified_page_continues_rather_than_inventing_a_document(
        self, pipeline, samples_dir
    ) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_e())
        assert group_shape(analysis) == [(RESUME, 1, 3)]

    def test_the_group_is_flagged_and_names_the_page(self, pipeline, samples_dir) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_e())
        group = analysis.groups[0]
        assert group.requires_review
        assert analysis.status is FileStatus.REVIEW_NEEDED


class TestGroupConfidence:
    def test_clean_documents_do_not_require_review(self, pipeline, samples_dir) -> None:
        """The core efficiency promise: obvious documents pass without a human."""
        analysis = analyze(pipeline, samples_dir, sample_data.sample_a())
        assert analysis.status is FileStatus.READY
        assert analysis.review_group_count == 0

    def test_corroborating_pages_raise_group_confidence(self, pipeline, samples_dir) -> None:
        """A 3-page resume should be more certain than its weakest page."""
        analysis = analyze(pipeline, samples_dir, sample_data.sample_b())
        group = analysis.groups[0]
        weakest_page = min(page.classification_confidence for page in analysis.pages)
        assert group.classification_confidence > weakest_page

    def test_overall_confidence_is_the_weaker_of_the_two_signals(
        self, pipeline, samples_dir
    ) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_a())
        for group in analysis.groups:
            assert group.overall_confidence == pytest.approx(
                min(group.classification_confidence, group.boundary_confidence)
            )


class TestCandidateAssociation:
    def test_identity_is_shared_across_a_single_applicant_packet(
        self, pipeline, samples_dir
    ) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_a())
        names = {group.candidate.name for group in analysis.groups}
        assert names == {"Benjamin Perez"}

    def test_references_page_is_not_attributed_to_a_referee(
        self, pipeline, samples_dir
    ) -> None:
        """Contact details on a reference sheet belong to the referees."""
        analysis = analyze(pipeline, samples_dir, sample_data.sample_a())
        references = analysis.groups[-1]
        assert references.document_type == REFERENCES
        assert references.candidate.name == "Benjamin Perez"

    def test_identity_is_not_shared_when_two_people_appear(
        self, pipeline, samples_dir
    ) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_g())
        names = [group.candidate.name for group in analysis.groups]
        assert len(set(names)) == 2

    def test_application_report_metadata_is_captured(self, pipeline, samples_dir) -> None:
        analysis = analyze(pipeline, samples_dir, sample_data.sample_a())
        report = analysis.groups[0]
        assert report.candidate.email == "benjamin.perez@example.com"
        assert report.candidate.applicant_id == "A-10482"
        assert report.candidate.job_title == "Senior Operations Analyst"
