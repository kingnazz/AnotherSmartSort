"""Synthetic coverage for PageUp cover-plus-resumes bulk compiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import SeparatorPolicy
from app.profiles.recruiting import APPLICATION_REPORT, COVER_LETTER, RESUME
from app.services.export_service import ExportService
from app.services.parsers.pageup import PageUpBulkCompileParser
from app.services.parsers.registry import build_default_registry
from app.services.text_features import extract_features
from scripts.pageup_fixtures import (
    PageUpResumeOnlyBatch,
    build_resume_only_compile,
)
from tests.helpers import build_pipeline


@pytest.fixture
def resume_only_pipeline(profile, thresholds):
    return build_pipeline(
        profile,
        thresholds,
        separator_policy=SeparatorPolicy.EXCLUDE,
        parser_registry=build_default_registry(profile),
    )


def features_of(batch: PageUpResumeOnlyBatch) -> list:
    return [extract_features("\n".join(page.lines)) for page in batch.pages]


def analyze(resume_only_pipeline, tmp_path: Path, batch: PageUpResumeOnlyBatch):
    return resume_only_pipeline.analyze_file(batch.write(tmp_path))


def resumes(analysis) -> list:
    return [group for group in analysis.groups if group.document_type == RESUME]


class TestResumeOnlyDetectionAndCover:
    def test_cover_declares_resume_only_roster_and_count(self, profile) -> None:
        batch = build_resume_only_compile()
        parser = PageUpBulkCompileParser(profile)
        cover = parser._parse_cover(features_of(batch))

        assert parser.can_parse(features_of(batch)).matched
        assert cover.declared_types == [RESUME]
        assert cover.declared_count == len(batch.applicants)
        assert [entry.display_name for entry in cover.roster] == [
            applicant.display_name for applicant in batch.applicants
        ]

    def test_declared_count_trims_generic_cover_footer_lines(self, profile) -> None:
        batch = build_resume_only_compile()
        count_index = next(
            index
            for index, line in enumerate(batch.pages[0].lines)
            if line.startswith("Number of Applicants")
        )
        batch.pages[0].lines[count_index:count_index] = [
            "Generated Applicant Listing",
            "Confidential Recruitment Services",
        ]
        cover = PageUpBulkCompileParser(profile)._parse_cover(features_of(batch))

        assert len(cover.roster) == len(batch.applicants)
        assert [entry.display_name for entry in cover.roster] == [
            applicant.display_name for applicant in batch.applicants
        ]

    def test_registry_uses_resume_only_mode(
        self, profile, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile()
        selection = build_default_registry(profile).select(features_of(batch))
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        assert selection.parser is not None
        assert selection.parser.name == "PageUp bulk compile"
        assert resume_only_pipeline.last_parse_outcome.metadata["mode"] == "resume_only"
        assert analysis.parser_name == "PageUp bulk compile"

    def test_metadata_cover_is_never_exported(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        analysis = analyze(resume_only_pipeline, tmp_path, build_resume_only_compile())

        cover = analysis.page(0)
        assert cover is not None and cover.is_excluded_separator
        assert all(0 not in group.export_page_indexes for group in analysis.groups)


class TestResumeOnlyRanges:
    def test_single_two_four_and_long_resumes_are_exact(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(lengths=(1, 2, 4, 7))
        analysis = analyze(resume_only_pipeline, tmp_path, batch)
        found = resumes(analysis)

        assert len(found) == 4
        assert [group.page_count for group in found] == [1, 2, 4, 7]
        assert [
            (group.start_page, group.end_page) for group in found
        ] == [
            (applicant.resume_first, applicant.resume_last)
            for applicant in batch.applicants
        ]
        assert found[-1].end_page == batch.page_count
        assert not [group for group in found if group.needs_attention]

    def test_pages_and_candidates_never_leak_between_roster_members(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(lengths=(2, 3, 1, 5))
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        for group, expected in zip(resumes(analysis), batch.applicants):
            assert group.candidate.name == expected.display_name
            assert [index + 1 for index in group.export_page_indexes] == (
                expected.resume_pages
            )


class TestResumeHeaderIdentity:
    @pytest.mark.parametrize(
        ("roster_name", "resume_header"),
        [
            ("Dr. Maren Vale", "Maren Vale"),
            ("Noel West, MBA", "Noel West, M.B.A."),
            ("Orion Xander (Ori)", "Ori Xander"),
            ("Priya Amara Young", "Priya Young"),
            ("Quinn R. Zephyr", "Quinn Zephyr"),
            ("RILEY ALDER", "riley alder"),
            ("Sage O'Connell", "Sage OConnell"),
            ("Talia River-Hart", "Talia River Hart"),
            ("Tomás Íbarra", "Tomas Ibarra"),
        ],
    )
    def test_safe_name_variations_open_the_expected_resume(
        self,
        resume_only_pipeline,
        tmp_path: Path,
        roster_name: str,
        resume_header: str,
    ) -> None:
        batch = build_resume_only_compile(
            roster=(roster_name, "Uma Juniper"),
            lengths=(2, 1),
            header_names=(resume_header, "Uma Juniper"),
        )
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        assert [(group.start_page, group.end_page) for group in resumes(analysis)] == [
            (2, 3),
            (4, 4),
        ]
        assert not [group for group in resumes(analysis) if group.needs_attention]

    def test_undeclared_preferred_name_can_match_a_unique_surname_header(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(
            roster=("Legal Orchard", "Parker Summit"),
            lengths=(1, 1),
            header_names=("Preferred Orchard", "Parker Summit"),
        )
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        assert [(group.start_page, group.end_page) for group in resumes(analysis)] == [
            (2, 2),
            (3, 3),
        ]

    def test_compact_unknown_suffix_and_date_layout_are_strong_together(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(
            roster=("Rowan Terrace", "Sawyer Union"),
            lengths=(1, 1),
            contact_flags=(False, True),
        )
        batch.pages[1].lines = [
            "Rowan Terrace CPHR Fellow",
            "Profile",
            "Invented programme and operations background.",
            "2020 - 2024",
            "2016 - 2020",
            "Programme planning",
            "Public workshop coordination",
            "Accessible service delivery",
            "Stakeholder communication",
            "Scheduling and reporting",
            "Community partnerships",
            "Process documentation",
        ]
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        assert [(group.start_page, group.end_page) for group in resumes(analysis)] == [
            (2, 2),
            (3, 3),
        ]

    def test_email_interleaved_between_name_columns_does_not_hide_identity(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(
            roster=("Taylor Willow", "Val Winter"),
            lengths=(1, 1),
            header_names=(
                "Taylor invented.candidate@example.com Willow",
                "Val Winter",
            ),
        )
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        assert [(group.start_page, group.end_page) for group in resumes(analysis)] == [
            (2, 2),
            (3, 3),
        ]


class TestOrderedStateMachine:
    def test_next_applicant_mention_inside_current_resume_is_not_a_boundary(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(
            roster=("Alex Morgan", "Taylor Brooks"), lengths=(3, 2)
        )
        batch.pages[2].lines = [
            "PROFESSIONAL EXPERIENCE (CONTINUED)",
            "Worked closely with Taylor Brooks, Director of Operations.",
            "2019 - 2022",
        ]
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        assert [(group.start_page, group.end_page) for group in resumes(analysis)] == [
            (2, 4),
            (5, 6),
        ]

    def test_later_roster_member_mention_cannot_jump_ahead(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(
            roster=("Arden Birch", "Blair Cedar", "Casey Dogwood"),
            lengths=(3, 2, 1),
        )
        batch.pages[2].lines = [
            "EXPERIENCE",
            "Partnered with Casey Dogwood on an invented community programme.",
        ]
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        assert [(group.start_page, group.end_page) for group in resumes(analysis)] == [
            (2, 4),
            (5, 6),
            (7, 7),
        ]

    def test_roster_member_listed_as_reference_is_not_a_boundary(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(
            roster=("Emery Fir", "Finley Grove"), lengths=(3, 1)
        )
        batch.pages[2].lines = [
            "REFERENCES",
            "Finley Grove",
            "finley.reference@example.com | (555) 010-5522",
        ]
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        assert [(group.start_page, group.end_page) for group in resumes(analysis)] == [
            (2, 4),
            (5, 5),
        ]

    def test_generic_resume_headings_never_create_documents(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(lengths=(7, 1, 2, 4))
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        assert len(resumes(analysis)) == len(batch.applicants)
        assert [group.page_count for group in resumes(analysis)] == [7, 1, 2, 4]


class TestResumeOnlySafeFailure:
    def test_missing_next_applicant_preserves_pages_and_needs_review(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(
            roster=("Gray Harbor", "Harper Indigo"),
            lengths=(2, 2),
            header_names=("Gray Harbor", "Jordan Kestrel"),
        )
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        found = resumes(analysis)
        assert len(found) == 1
        assert found[0].export_page_indexes == [1, 2, 3, 4]
        assert found[0].needs_attention
        assert analysis.review_group_count == 1
        assert any("confidently locate" in reason for reason in found[0].review_reasons)

    def test_identity_without_header_layout_is_ambiguous_and_needs_review(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(
            roster=("Indigo Lake", "Jules Meadow"), lengths=(2, 2)
        )
        second_start = batch.applicants[1].resume_first - 1
        batch.pages[second_start].lines = [
            "Jules Meadow",
            "Candidate profile",
            "General background information",
        ]
        analysis = analyze(resume_only_pipeline, tmp_path, batch)

        found = resumes(analysis)
        assert len(found) == 1
        assert found[0].end_page == batch.page_count
        assert found[0].needs_attention


class TestResumeOnlyExport:
    def test_resume_filter_exports_exact_count_under_timestamped_folder(
        self, resume_only_pipeline, tmp_path: Path
    ) -> None:
        batch = build_resume_only_compile(lengths=(1, 2, 4, 7))
        analysis = analyze(resume_only_pipeline, tmp_path, batch)
        result = ExportService(
            group_by_document_type=True,
            export_combined_packets=False,
            document_types=[RESUME],
            batch_folder=True,
        ).export([analysis], tmp_path / "out")

        assert result.document_count == len(batch.applicants)
        assert all(item.output_path.parent.name == "Resumes" for item in result.exported)
        assert all(item.output_path.parent.parent == result.output_directory for item in result.exported)
        assert not [
            group
            for group in analysis.groups
            if group.document_type in (APPLICATION_REPORT, COVER_LETTER)
        ]
