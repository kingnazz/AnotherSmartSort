"""Deterministic ATS report parser: detection, exact page ranges, multi-applicant.

Regression coverage for the real client export structure: an application
report, a "Resume" separator, the resume, a "Cover Letters" separator, the
cover letter -- reproduced synthetically in ``scripts/ats_fixtures.py`` with
the exact page counts observed in the real files (never committed here).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import ClassificationSource
from app.profiles.recruiting import APPLICATION_REPORT, COVER_LETTER, RESUME
from app.services.ats_parser import AtsReportParser
from app.services.export_service import ExportService
from app.services.text_features import extract_features
from scripts import ats_fixtures
from scripts.ats_fixtures import (
    marcus_delgado_batch,
    nathan_whitfield_batch,
    build_multi_applicant_batch,
    trevor_hollands_batch,
    sofia_brennan_batch,
)
from scripts.sample_data import SampleDocument, SamplePage, build_pdf, paragraph, separator_page


def _joined_features(page: SamplePage):
    return extract_features("\n".join(page.lines))


class TestDetection:
    """Whole-file detection: does this PDF match a known ATS export?"""

    def test_a_known_ats_export_is_detected(self, profile) -> None:
        batch = marcus_delgado_batch()
        features = [_joined_features(page) for page in batch.pages]
        assert AtsReportParser(profile).looks_like_ats_export(features)

    def test_a_generic_resume_only_file_is_not_detected(self, profile) -> None:
        from scripts.sample_data import resume_pages

        features = [_joined_features(page) for page in resume_pages(total=3)]
        assert not AtsReportParser(profile).looks_like_ats_export(features)

    def test_a_generic_application_report_sample_is_not_detected(self, profile) -> None:
        """The pre-existing generic fixture's header does not carry the two
        strong markers a real ATS export does -- it must not falsely engage
        the deterministic parser."""
        from scripts.sample_data import application_report_pages

        features = [_joined_features(page) for page in application_report_pages(total=4)]
        assert not AtsReportParser(profile).looks_like_ats_export(features)


class TestNamedCandidateFixtures:
    """Point-for-point reproduction of the real client file structures."""

    @pytest.mark.parametrize(
        "factory",
        [marcus_delgado_batch, nathan_whitfield_batch, trevor_hollands_batch, sofia_brennan_batch],
        ids=["marcus_delgado", "nathan_whitfield", "trevor_hollands", "sofia_brennan"],
    )
    def test_exact_export_page_ranges(self, ats_pipeline, ats_samples_dir: Path, factory) -> None:
        batch = factory()
        expected = batch.candidates[0]
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)

        assert [g.document_type for g in analysis.groups] == [
            APPLICATION_REPORT,
            RESUME,
            COVER_LETTER,
        ], "the file must split into exactly three documents, in order"

        report, resume, cover_letter = analysis.groups

        assert sorted(report.export_page_indexes) == expected.report.page_indexes
        assert sorted(resume.export_page_indexes) == expected.resume.page_indexes
        assert sorted(cover_letter.export_page_indexes) == expected.cover_letter.page_indexes

        # The separator pages themselves must never appear in the export.
        assert (expected.resume_separator_page - 1) not in resume.export_page_indexes
        assert (expected.cover_letter_separator_page - 1) not in cover_letter.export_page_indexes

        for group in analysis.groups:
            assert group.candidate.name == expected.name
            assert not group.needs_attention, group.review_reasons
            assert group.classification_confidence >= 0.98
            assert group.boundary_confidence >= 0.98
            assert group.classification_source is ClassificationSource.DETERMINISTIC

    @pytest.mark.parametrize(
        "factory",
        [marcus_delgado_batch, nathan_whitfield_batch, trevor_hollands_batch, sofia_brennan_batch],
        ids=["marcus_delgado", "nathan_whitfield", "trevor_hollands", "sofia_brennan"],
    )
    def test_multi_page_documents_stay_as_one_document(
        self, ats_pipeline, ats_samples_dir: Path, factory
    ) -> None:
        """A six-page resume is ONE document, not six, however many pages it has."""
        batch = factory()
        expected = batch.candidates[0]
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)

        resume = next(g for g in analysis.groups if g.document_type == RESUME)
        assert len(resume.export_page_indexes) == expected.resume.page_count

        cover_letter = next(g for g in analysis.groups if g.document_type == COVER_LETTER)
        assert len(cover_letter.export_page_indexes) == expected.cover_letter.page_count

    def test_trevor_hollands_six_page_resume_and_three_page_cover_letter(
        self, ats_pipeline, ats_samples_dir: Path
    ) -> None:
        """The exact worked example from the specification."""
        batch = trevor_hollands_batch()
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)

        resume = next(g for g in analysis.groups if g.document_type == RESUME)
        cover_letter = next(g for g in analysis.groups if g.document_type == COVER_LETTER)
        assert [p + 1 for p in sorted(resume.export_page_indexes)] == [12, 13, 14, 15, 16, 17]
        assert [p + 1 for p in sorted(cover_letter.export_page_indexes)] == [19, 20, 21]

    def test_second_separator_transition_types_correctly(
        self, ats_pipeline, ats_samples_dir: Path
    ) -> None:
        """Regression test for the original bug: after the SECOND separator
        ("Cover Letters"), the new group must be typed Cover Letter -- never
        inherit the Resume type the first separator opened."""
        batch = trevor_hollands_batch()
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)
        assert [g.document_type for g in analysis.groups][-1] == COVER_LETTER


class TestIdentityPropagation:
    def test_resume_with_no_name_still_inherits_the_report_candidate(
        self, ats_pipeline, tmp_path: Path
    ) -> None:
        """The resume/cover letter never has to repeat the candidate's name --
        identity comes from the ATS report and propagates forward."""
        anonymous_resume = SamplePage(
            lines=[
                "PROFESSIONAL SUMMARY",
                *paragraph(
                    "Experienced operations professional with a strong record of "
                    "delivering measurable results across cross-functional teams."
                ),
                "",
                "EXPERIENCE",
                "Senior Analyst, Example Co, 2020 - Present",
            ]
        )
        pages = [
            *ats_fixtures.ats_report_pages(
                name="Morgan Ellis",
                applicant_id="A-99001",
                email="morgan.ellis@example.com",
                phone="(555) 909-1122",
                job_title="Data Analyst",
                total=2,
            ),
            separator_page("Resume"),
            anonymous_resume,
        ]
        document = SampleDocument(filename="anonymous_resume.pdf", description="", pages=pages)
        path = build_pdf(document, tmp_path / document.filename)

        analysis = ats_pipeline.analyze_file(path)
        resume = next(g for g in analysis.groups if g.document_type == RESUME)
        assert resume.candidate.name == "Morgan Ellis"
        assert not resume.needs_attention


class TestMultiApplicantBatch:
    """~80 pages, many applicants concatenated -- the real client workflow."""

    def test_every_applicant_is_detected_with_no_leakage(
        self, ats_pipeline, ats_samples_dir: Path
    ) -> None:
        batch = build_multi_applicant_batch()
        assert batch.page_count >= 75, "fixture should be roughly 80 pages"
        assert len(batch.candidates) >= 8

        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)

        assert len(analysis.packets) == len(batch.candidates)
        assert {p.display_name for p in analysis.packets} == {c.name for c in batch.candidates}

        for expected in batch.candidates:
            report = next(
                g
                for g in analysis.groups
                if g.document_type == APPLICATION_REPORT
                and g.start_page == expected.report.first_page
            )
            resume = next(
                g
                for g in analysis.groups
                if g.document_type == RESUME and g.start_page == expected.resume_separator_page
            )
            cover_letter = next(
                g
                for g in analysis.groups
                if g.document_type == COVER_LETTER
                and g.start_page == expected.cover_letter_separator_page
            )

            assert sorted(report.export_page_indexes) == expected.report.page_indexes
            assert sorted(resume.export_page_indexes) == expected.resume.page_indexes
            assert (
                sorted(cover_letter.export_page_indexes) == expected.cover_letter.page_indexes
            )

            for group in (report, resume, cover_letter):
                assert group.candidate.name == expected.name, (
                    f"{group.document_type} pages {group.page_range_label} leaked into "
                    f"{group.candidate.name!r} instead of {expected.name!r}"
                )
                # Every exported page must fall inside this candidate's own
                # span -- the one thing that would be catastrophic to get
                # wrong in a concatenated multi-applicant file.
                for index in group.export_page_indexes:
                    assert index + 1 in expected.all_pages, (
                        f"page {index + 1} belongs to {expected.name} but is outside "
                        f"their own page range {expected.all_pages}"
                    )

    def test_separator_pages_are_excluded_from_every_export(
        self, ats_pipeline, ats_samples_dir: Path
    ) -> None:
        batch = build_multi_applicant_batch()
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)

        exported_pages = {
            index + 1 for group in analysis.groups for index in group.export_page_indexes
        }
        for expected in batch.candidates:
            assert expected.resume_separator_page not in exported_pages
            assert expected.cover_letter_separator_page not in exported_pages

    def test_nothing_needs_review(self, ats_pipeline, ats_samples_dir: Path) -> None:
        batch = build_multi_applicant_batch()
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)
        flagged = [g for g in analysis.groups if g.needs_attention]
        assert not flagged, [(g.document_type, g.review_reasons) for g in flagged]
        assert not any(p.requires_review for p in analysis.packets)

    def test_output_filenames_match_candidate_names(
        self, ats_pipeline, ats_samples_dir: Path, tmp_path: Path
    ) -> None:
        batch = build_multi_applicant_batch()
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)

        destination = tmp_path / "out"
        ExportService(group_by_document_type=True, export_combined_packets=False).export(
            [analysis], destination
        )

        resume_names = {p.stem for p in (destination / "Resumes").glob("*.pdf")}
        expected_names = {c.name for c in batch.candidates}
        assert resume_names == expected_names


class TestGenericFilesAreUnaffectedByTheAtsParser:
    """The fast path must never engage on a file that does not match it."""

    def test_generic_sample_a_is_identical_with_or_without_the_parser(
        self, pipeline, ats_pipeline, samples_dir: Path
    ) -> None:
        from scripts import sample_data

        path = samples_dir / sample_data.sample_a().filename
        without = pipeline.analyze_file(path)
        with_parser = ats_pipeline.analyze_file(path)

        assert [g.document_type for g in without.groups] == [
            g.document_type for g in with_parser.groups
        ]
        assert [g.export_page_indexes for g in without.groups] == [
            g.export_page_indexes for g in with_parser.groups
        ]
        assert all(
            g.classification_source is not ClassificationSource.DETERMINISTIC
            for g in with_parser.groups
        )

    def test_generic_separator_sample_is_unaffected(
        self, pipeline, ats_pipeline, samples_dir: Path
    ) -> None:
        from scripts import sample_data

        path = samples_dir / sample_data.sample_f().filename
        without = pipeline.analyze_file(path)
        with_parser = ats_pipeline.analyze_file(path)
        assert [g.document_type for g in without.groups] == [
            g.document_type for g in with_parser.groups
        ]


class TestExportFolderStructure:
    """Document-type-first output: Resumes/, Cover Letters/, ..., Needs Review/."""

    def test_folders_are_named_by_type_and_files_by_candidate(
        self, ats_pipeline, ats_samples_dir: Path, tmp_path: Path
    ) -> None:
        batch = trevor_hollands_batch()
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)

        destination = tmp_path / "out"
        ExportService(group_by_document_type=True, export_combined_packets=False).export(
            [analysis], destination
        )

        assert (destination / "Application Reports" / "Trevor Hollands.pdf").exists()
        assert (destination / "Resumes" / "Trevor Hollands.pdf").exists()
        assert (destination / "Cover Letters" / "Trevor Hollands.pdf").exists()
        assert not (destination / "Trevor Hollands").exists(), (
            "no per-candidate folder should exist in this layout"
        )

    def test_collisions_get_a_numeric_suffix_with_spaces_preserved(
        self, ats_pipeline, ats_samples_dir: Path, tmp_path: Path
    ) -> None:
        batch = sofia_brennan_batch()
        analysis = ats_pipeline.analyze_file(ats_samples_dir / batch.filename)

        destination = tmp_path / "out"
        service = ExportService(group_by_document_type=True, export_combined_packets=False)
        service.export([analysis], destination)
        service.export([analysis], destination)  # run again: must collide, not overwrite

        names = sorted(p.name for p in (destination / "Resumes").glob("*.pdf"))
        assert names == ["Sofia Brennan.pdf", "Sofia Brennan_2.pdf"]

    def test_unknown_candidate_gets_a_sequential_filename(self, tmp_path: Path) -> None:
        from app.models.candidate import Candidate
        from app.models.document import DocumentGroup
        from app.models.enums import FileStatus
        from app.models.source_file import SourceFileAnalysis

        group = DocumentGroup(
            source_pdf=str(tmp_path / "x.pdf"), page_indexes=[0], document_type=RESUME
        )
        group.candidate = Candidate()
        analysis = SourceFileAnalysis(path=tmp_path / "x.pdf", status=FileStatus.READY)
        analysis.groups = [group]

        import pymupdf

        doc = pymupdf.open()
        doc.new_page()
        doc.save(str(tmp_path / "x.pdf"))
        doc.close()

        destination = tmp_path / "out"
        ExportService(group_by_document_type=True, export_combined_packets=False).export(
            [analysis], destination
        )
        names = [p.name for p in (destination / "Resumes").glob("*.pdf")]
        assert names == ["Unknown_001.pdf"]

    def test_flagged_documents_are_routed_to_needs_review(
        self, pipeline, samples_dir: Path, tmp_path: Path
    ) -> None:
        """A document that still needs review is filed separately from the
        type folders, regardless of what type it was predicted to be."""
        from scripts import sample_data

        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_e().filename)
        assert len(analysis.groups) == 1
        assert analysis.groups[0].needs_attention, "fixture is expected to need review"
        assert analysis.groups[0].document_type == RESUME

        destination = tmp_path / "out"
        ExportService(group_by_document_type=True, export_combined_packets=False).export(
            [analysis], destination
        )

        assert [p.name for p in (destination / "Needs Review").glob("*.pdf")] == [
            "Benjamin Perez.pdf"
        ]
        assert not (destination / "Resumes").exists()
