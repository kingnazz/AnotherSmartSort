"""The review workflow: manual corrections must re-derive group state."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.enums import FileStatus, SeparatorPolicy, SeparatorState
from app.profiles.recruiting import COVER_LETTER, REFERENCES, RESUME
from app.services.export_service import ExportService
from app.services.grouping_service import GroupingService
from app.services.pdf_service import open_pdf
from scripts import sample_data

from tests.helpers import build_pipeline, group_shape


@pytest.fixture
def analysis(pipeline, samples_dir: Path):
    return pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)


@pytest.fixture
def grouping(profile, thresholds) -> GroupingService:
    return GroupingService(profile, thresholds)


class TestChangeDocumentType:
    def test_type_is_updated(self, grouping, analysis) -> None:
        group = analysis.groups[1]
        grouping.set_document_type(analysis, group, "Writing Sample")
        assert group.document_type == "Writing Sample"

    def test_correction_clears_review_and_pins_confidence(self, grouping, analysis) -> None:
        group = analysis.groups[1]
        group.add_review_reason("something uncertain")
        grouping.set_document_type(analysis, group, "Writing Sample")

        assert not group.requires_review
        assert group.type_manually_set
        assert group.overall_confidence == 1.0

    def test_member_pages_are_updated(self, grouping, analysis) -> None:
        group = analysis.groups[1]
        grouping.set_document_type(analysis, group, "Portfolio")
        for index in group.page_indexes:
            assert analysis.page(index).predicted_type == "Portfolio"

    def test_unknown_type_is_normalised(self, grouping, analysis) -> None:
        group = analysis.groups[1]
        grouping.set_document_type(analysis, group, "CV")
        assert group.document_type == RESUME

    def test_manual_type_survives_a_refresh(self, grouping, analysis) -> None:
        group = analysis.groups[1]
        grouping.set_document_type(analysis, group, "Portfolio")
        grouping.refresh_group(group, analysis.pages)
        assert group.document_type == "Portfolio"


class TestSplit:
    def test_split_creates_two_groups(self, grouping, analysis) -> None:
        before = len(analysis.groups)
        grouping.split_before(analysis, 5)  # page 6, inside the resume
        assert len(analysis.groups) == before + 1

    def test_split_divides_the_pages_correctly(self, grouping, analysis) -> None:
        grouping.split_before(analysis, 5)
        shape = group_shape(analysis)
        assert (RESUME, 5, 5) in shape
        assert any(start == 6 and end == 7 for _type, start, end in shape)

    def test_split_at_a_group_start_is_a_no_op(self, grouping, analysis) -> None:
        before = len(analysis.groups)
        assert grouping.split_before(analysis, 4) is None
        assert len(analysis.groups) == before

    def test_every_page_is_still_assigned_after_a_split(self, grouping, analysis) -> None:
        grouping.split_before(analysis, 5)
        assigned = sorted(i for g in analysis.groups for i in g.page_indexes)
        assert assigned == list(range(analysis.page_count))

    def test_groups_stay_in_page_order(self, grouping, analysis) -> None:
        grouping.split_before(analysis, 5)
        starts = [group.start_page_index for group in analysis.groups]
        assert starts == sorted(starts)


class TestMerge:
    def test_merge_with_previous(self, grouping, analysis) -> None:
        before = len(analysis.groups)
        merged = grouping.merge_with_previous(analysis, analysis.groups[1])

        assert len(analysis.groups) == before - 1
        assert merged.page_indexes == list(range(0, 7))

    def test_merge_with_next(self, grouping, analysis) -> None:
        before = len(analysis.groups)
        merged = grouping.merge_with_next(analysis, analysis.groups[0])

        assert len(analysis.groups) == before - 1
        assert merged.page_count == 7

    def test_merge_with_previous_on_the_first_group_is_a_no_op(
        self, grouping, analysis
    ) -> None:
        assert grouping.merge_with_previous(analysis, analysis.groups[0]) is None

    def test_merge_with_next_on_the_last_group_is_a_no_op(self, grouping, analysis) -> None:
        assert grouping.merge_with_next(analysis, analysis.groups[-1]) is None

    def test_split_then_merge_restores_the_original_shape(self, grouping, analysis) -> None:
        original = group_shape(analysis)
        grouping.split_before(analysis, 5)
        target = next(g for g in analysis.groups if g.start_page_index == 5)
        grouping.merge_with_previous(analysis, target)
        assert group_shape(analysis) == original

    def test_every_page_is_still_assigned_after_a_merge(self, grouping, analysis) -> None:
        grouping.merge_with_previous(analysis, analysis.groups[1])
        assigned = sorted(i for g in analysis.groups for i in g.page_indexes)
        assert assigned == list(range(analysis.page_count))


class TestExclude:
    def test_excluded_group_is_skipped_on_export(
        self, grouping, analysis, tmp_path: Path
    ) -> None:
        grouping.set_group_excluded(analysis, analysis.groups[0], True)
        result = ExportService().export([analysis], tmp_path)
        assert result.document_count == 3

    def test_exclusion_can_be_undone(self, grouping, analysis, tmp_path: Path) -> None:
        group = analysis.groups[0]
        grouping.set_group_excluded(analysis, group, True)
        grouping.set_group_excluded(analysis, group, False)
        assert ExportService().export([analysis], tmp_path).document_count == 4

    def test_excluded_groups_are_not_counted_as_active(self, grouping, analysis) -> None:
        grouping.set_group_excluded(analysis, analysis.groups[0], True)
        assert len(analysis.active_groups) == 3


class TestSeparatorOverride:
    def test_user_can_drop_a_separator_page_from_output(
        self, profile, thresholds, samples_dir: Path, tmp_path: Path
    ) -> None:
        pipeline = build_pipeline(profile, thresholds, separator_policy=SeparatorPolicy.INCLUDE)
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_f().filename)
        grouping = GroupingService(profile, thresholds)
        group = analysis.groups[0]

        assert group.export_page_indexes == [0, 1, 2]
        grouping.set_separator_included(analysis, group, 0, False)
        assert group.export_page_indexes == [1, 2]

        ExportService().export([analysis], tmp_path)
        resume = next(tmp_path.rglob("*Resume*.pdf"))
        with open_pdf(resume) as document:
            assert document.page_count == 2

    def test_user_can_keep_a_separator_page(
        self, profile, thresholds, samples_dir: Path
    ) -> None:
        pipeline = build_pipeline(profile, thresholds, separator_policy=SeparatorPolicy.EXCLUDE)
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_f().filename)
        grouping = GroupingService(profile, thresholds)
        group = analysis.groups[0]

        assert group.export_page_indexes == [1, 2]
        grouping.set_separator_included(analysis, group, 0, True)
        assert group.export_page_indexes == [0, 1, 2]
        assert analysis.page(0).separator_state is SeparatorState.INCLUDED


class TestReviewStatus:
    def test_accepting_a_group_clears_its_review_flag(
        self, profile, thresholds, samples_dir: Path
    ) -> None:
        pipeline = build_pipeline(profile, thresholds)
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_e().filename)
        grouping = GroupingService(profile, thresholds)
        group = analysis.groups[0]

        assert group.requires_review
        grouping.mark_reviewed(analysis, group)
        assert not group.requires_review
        assert analysis.status is FileStatus.READY

    def test_file_status_follows_the_remaining_review_items(
        self, profile, thresholds, samples_dir: Path
    ) -> None:
        pipeline = build_pipeline(profile, thresholds)
        analysis = pipeline.analyze_file(samples_dir / sample_data.sample_e().filename)
        grouping = GroupingService(profile, thresholds)

        assert analysis.status is FileStatus.REVIEW_NEEDED
        for group in list(analysis.groups):
            grouping.mark_reviewed(analysis, group)
        assert analysis.status is FileStatus.READY


class TestCorrectionsAffectExport:
    def test_a_retyped_group_is_exported_under_the_new_name(
        self, grouping, analysis, tmp_path: Path
    ) -> None:
        grouping.set_document_type(analysis, analysis.groups[3], COVER_LETTER)
        ExportService().export([analysis], tmp_path)
        names = sorted(p.name for p in (tmp_path / "Benjamin Perez").glob("*.pdf"))
        assert "Benjamin_Perez_Cover_Letter_2.pdf" in names
        assert not any(REFERENCES.replace(" ", "_") in name for name in names)

    def test_a_merged_group_exports_as_one_pdf(
        self, grouping, analysis, tmp_path: Path
    ) -> None:
        grouping.merge_with_previous(analysis, analysis.groups[1])
        result = ExportService().export([analysis], tmp_path)

        assert result.document_count == 3
        merged = next(d for d in result.exported if d.group.page_count == 7)
        with open_pdf(merged.output_path) as document:
            assert document.page_count == 7

    def test_a_split_group_exports_as_two_pdfs(
        self, grouping, analysis, tmp_path: Path
    ) -> None:
        grouping.split_before(analysis, 5)
        result = ExportService().export([analysis], tmp_path)
        assert result.document_count == 5
        total_pages = sum(len(d.page_numbers) for d in result.exported)
        assert total_pages == analysis.page_count
