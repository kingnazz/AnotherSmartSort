"""Correction commands and undo/redo, tested at the domain level.

Drag and drop is a way of *invoking* these; what matters for correctness is
that the underlying model ends up right, and that every correction can be
taken back. Both are asserted here without Qt, so a failure points at the
domain rather than at event simulation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.profiles.recruiting import APPLICATION_REPORT, COVER_LETTER, RESUME
from app.services.correction_history import CorrectionHistory
from scripts import sample_data


@pytest.fixture
def analysis(pipeline, samples_dir: Path):
    """A real analysed packet: application report, resume, cover letter, references."""
    return pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)


@pytest.fixture
def history() -> CorrectionHistory:
    return CorrectionHistory()


def types_of(analysis) -> list[str]:
    return [g.document_type for g in analysis.groups]


def shape_of(analysis) -> list[tuple[str, int, int]]:
    return [(g.document_type, g.start_page, g.end_page) for g in analysis.groups]


class TestRetype:
    def test_a_document_can_be_retyped(self, grouping, analysis) -> None:
        group = analysis.groups[1]
        grouping.set_document_type(analysis, group, COVER_LETTER)
        assert group.document_type == COVER_LETTER

    def test_retype_is_undoable_and_redoable(self, grouping, analysis, history) -> None:
        before = types_of(analysis)
        group = analysis.groups[1]

        history.record(
            "Change type to Cover Letter",
            analysis,
            lambda: grouping.set_document_type(analysis, group, COVER_LETTER),
        )
        assert types_of(analysis) != before

        history.undo(analysis)
        assert types_of(analysis) == before

        history.redo(analysis)
        assert types_of(analysis)[1] == COVER_LETTER

    def test_the_label_describes_what_will_be_undone(
        self, grouping, analysis, history
    ) -> None:
        history.record(
            "Change type to Cover Letter",
            analysis,
            lambda: grouping.set_document_type(analysis, analysis.groups[1], COVER_LETTER),
        )
        assert history.undo_label == "Change type to Cover Letter"
        history.undo(analysis)
        assert history.redo_label == "Change type to Cover Letter"


class TestSplitAndMerge:
    def test_split_is_undoable(self, grouping, analysis, history) -> None:
        before = shape_of(analysis)
        target = next(g for g in analysis.groups if g.page_count > 1)
        seam = target.page_indexes[1]

        history.record("Split", analysis, lambda: grouping.split_before(analysis, seam))
        assert len(analysis.groups) == len(before) + 1

        history.undo(analysis)
        assert shape_of(analysis) == before

    def test_merge_is_undoable(self, grouping, analysis, history) -> None:
        before = shape_of(analysis)
        second = analysis.groups[1]

        history.record(
            "Merge with previous",
            analysis,
            lambda: grouping.merge_with_previous(analysis, second),
        )
        assert len(analysis.groups) == len(before) - 1

        history.undo(analysis)
        assert shape_of(analysis) == before

    def test_split_then_merge_undoes_in_order(self, grouping, analysis, history) -> None:
        original = shape_of(analysis)
        target = next(g for g in analysis.groups if g.page_count > 1)
        seam = target.page_indexes[1]

        history.record("Split", analysis, lambda: grouping.split_before(analysis, seam))
        after_split = shape_of(analysis)

        history.record(
            "Merge",
            analysis,
            lambda: grouping.merge_with_previous(analysis, analysis.groups[1]),
        )

        history.undo(analysis)
        assert shape_of(analysis) == after_split
        history.undo(analysis)
        assert shape_of(analysis) == original


class TestPageMoves:
    def test_a_page_moves_to_the_previous_document(self, grouping, analysis) -> None:
        first, second = analysis.groups[0], analysis.groups[1]
        moving = second.page_indexes[0]

        assert grouping.move_pages(analysis, [moving], first)
        assert moving in first.page_indexes
        assert moving not in second.page_indexes

    def test_a_page_moves_to_the_next_document(self, grouping, analysis) -> None:
        first, second = analysis.groups[0], analysis.groups[1]
        moving = first.page_indexes[-1]

        assert grouping.move_pages(analysis, [moving], second)
        assert moving in second.page_indexes
        assert moving not in first.page_indexes

    def test_source_page_order_is_preserved(self, grouping, analysis) -> None:
        first, second = analysis.groups[0], analysis.groups[1]
        grouping.move_pages(analysis, [first.page_indexes[-1]], second)
        assert second.page_indexes == sorted(second.page_indexes)
        assert first.page_indexes == sorted(first.page_indexes)

    def test_a_contiguous_block_moves_together(self, grouping, analysis) -> None:
        first, second = analysis.groups[0], analysis.groups[1]
        block = first.page_indexes[-2:]

        assert grouping.move_pages(analysis, block, second)
        for index in block:
            assert index in second.page_indexes
        assert second.page_indexes == sorted(second.page_indexes)

    def test_a_non_contiguous_selection_is_refused(self, grouping, analysis) -> None:
        """Rather than corrupting page order."""
        first, second = analysis.groups[0], analysis.groups[1]
        scattered = [first.page_indexes[0], first.page_indexes[-1]]
        if len(first.page_indexes) < 3:
            pytest.skip("needs a group of at least three pages")

        allowed, reason = grouping.can_move_pages(analysis, scattered, second)
        assert not allowed
        assert "next to each other" in reason
        assert not grouping.move_pages(analysis, scattered, second)

    def test_a_move_leaving_a_gap_is_refused(self, grouping, analysis) -> None:
        first = analysis.groups[0]
        last = analysis.groups[-1]
        if first.page_count < 2:
            pytest.skip("needs a multi-page first group")

        # Moving the first group's opening page into the last group would
        # leave the last group discontiguous.
        allowed, reason = grouping.can_move_pages(analysis, [first.page_indexes[0]], last)
        assert not allowed
        assert "gap" in reason or "split the other document" in reason

    def test_a_cross_file_move_is_refused(self, grouping, analysis) -> None:
        from app.models.document import DocumentGroup

        foreign = DocumentGroup(source_pdf="/somewhere/else.pdf", page_indexes=[0])
        allowed, reason = grouping.can_move_pages(
            analysis, [analysis.groups[0].page_indexes[0]], foreign
        )
        assert not allowed
        assert "same PDF" in reason

    def test_a_page_move_is_undoable(self, grouping, analysis, history) -> None:
        before = shape_of(analysis)
        first, second = analysis.groups[0], analysis.groups[1]
        moving = first.page_indexes[-1]

        history.record(
            "Move page", analysis, lambda: grouping.move_pages(analysis, [moving], second)
        )
        assert shape_of(analysis) != before

        history.undo(analysis)
        assert shape_of(analysis) == before

        history.redo(analysis)
        assert moving in analysis.groups[1].page_indexes

    def test_emptying_a_group_removes_it(self, grouping, analysis) -> None:
        single = next((g for g in analysis.groups if g.page_count == 1), None)
        if single is None:
            pytest.skip("needs a single-page group")
        position = analysis.groups.index(single)
        neighbour = analysis.groups[position - 1]

        grouping.move_pages(analysis, list(single.page_indexes), neighbour)
        assert single not in analysis.groups


class TestSplitByDrag:
    def test_trailing_pages_become_a_new_document(self, grouping, analysis) -> None:
        target = next(g for g in analysis.groups if g.page_count > 1)
        tail = target.page_indexes[-1:]

        created = grouping.split_into_new_document(analysis, tail)
        assert created is not None
        assert created.page_indexes == tail
        assert tail[0] not in target.page_indexes

    def test_taking_every_page_is_refused(self, grouping, analysis) -> None:
        """That is not a split; it would leave an empty document behind."""
        target = analysis.groups[0]
        assert grouping.split_into_new_document(analysis, list(target.page_indexes)) is None

    def test_taking_from_the_middle_is_refused(self, grouping, analysis) -> None:
        target = next((g for g in analysis.groups if g.page_count >= 3), None)
        if target is None:
            pytest.skip("needs a group of at least three pages")
        middle = [target.page_indexes[1]]
        assert grouping.split_into_new_document(analysis, middle) is None

    def test_a_drag_split_is_undoable(self, grouping, analysis, history) -> None:
        before = shape_of(analysis)
        target = next(g for g in analysis.groups if g.page_count > 1)
        tail = target.page_indexes[-1:]

        history.record(
            "Split into new document",
            analysis,
            lambda: grouping.split_into_new_document(analysis, tail),
        )
        history.undo(analysis)
        assert shape_of(analysis) == before


class TestCandidateReassignment:
    def test_a_document_moves_to_another_candidate(self, packets, analysis) -> None:
        if len(analysis.packets) < 2:
            packets.create_packet_for(analysis, analysis.groups[-1], "Someone Else")
        target = analysis.packets[-1]
        document = analysis.groups[0]

        packets.move_document(analysis, document, target)
        assert document.packet_id == target.id

    def test_reassignment_is_undoable(self, packets, analysis, history) -> None:
        packets.create_packet_for(analysis, analysis.groups[-1], "Someone Else")
        original = analysis.groups[0].packet_id
        target = analysis.packets[-1]
        document = analysis.groups[0]

        history.record(
            "Move to candidate",
            analysis,
            lambda: packets.move_document(analysis, document, target),
        )
        assert analysis.groups[0].packet_id != original

        history.undo(analysis)
        assert analysis.groups[0].packet_id == original

    def test_a_rename_is_undoable(self, packets, analysis, history) -> None:
        packet = analysis.identified_packets[0]
        original = packet.candidate.name

        history.record(
            "Rename candidate",
            analysis,
            lambda: packets.rename_candidate(packet, "Renamed Person"),
        )
        assert analysis.identified_packets[0].candidate.name == "Renamed Person"

        history.undo(analysis)
        assert analysis.identified_packets[0].candidate.name == original


class TestExclusion:
    def test_excluding_a_document_is_undoable(self, grouping, analysis, history) -> None:
        group = analysis.groups[0]
        history.record(
            "Exclude document",
            analysis,
            lambda: grouping.set_group_excluded(analysis, group, True),
        )
        assert analysis.groups[0].excluded

        history.undo(analysis)
        assert not analysis.groups[0].excluded


class TestHistoryBehaviour:
    def test_nothing_to_undo_at_the_start(self, history, analysis) -> None:
        assert not history.can_undo
        assert not history.can_redo
        assert history.undo(analysis) is None
        assert history.redo(analysis) is None

    def test_a_correction_that_changes_nothing_is_not_recorded(
        self, grouping, analysis, history
    ) -> None:
        """An undo entry that appears to do nothing is worse than none."""
        history.record("No-op", analysis, lambda: None)
        assert not history.can_undo

    def test_a_new_correction_clears_the_redo_branch(
        self, grouping, analysis, history
    ) -> None:
        history.record(
            "First",
            analysis,
            lambda: grouping.set_document_type(analysis, analysis.groups[1], COVER_LETTER),
        )
        history.undo(analysis)
        assert history.can_redo

        history.record(
            "Second",
            analysis,
            lambda: grouping.set_document_type(analysis, analysis.groups[0], RESUME),
        )
        assert not history.can_redo, "a redo branch survived a divergent correction"

    def test_repeated_undo_and_redo_stay_consistent(
        self, grouping, analysis, history
    ) -> None:
        """The stack's own copies must not be mutated by later work."""
        before = shape_of(analysis)
        target = next(g for g in analysis.groups if g.page_count > 1)
        seam = target.page_indexes[1]
        history.record("Split", analysis, lambda: grouping.split_before(analysis, seam))
        after = shape_of(analysis)

        for _ in range(3):
            history.undo(analysis)
            assert shape_of(analysis) == before
            history.redo(analysis)
            assert shape_of(analysis) == after

    def test_the_stack_is_bounded(self, grouping, analysis) -> None:
        history = CorrectionHistory(depth=3)
        for index in range(6):
            document_type = COVER_LETTER if index % 2 == 0 else RESUME
            history.record(
                f"Change {index}",
                analysis,
                lambda t=document_type: grouping.set_document_type(
                    analysis, analysis.groups[1], t
                ),
            )
        assert len(history._undo) == 3

    def test_undo_does_not_reanalyse(self, grouping, analysis, history, monkeypatch) -> None:
        """Re-analysing would throw away every other correction."""
        target = next(g for g in analysis.groups if g.page_count > 1)
        history.record(
            "Split",
            analysis,
            lambda: grouping.split_before(analysis, target.page_indexes[1]),
        )

        import app.services.processing_service as processing

        def explode(*_args, **_kwargs):  # pragma: no cover - must never run
            raise AssertionError("undo re-ran analysis")

        monkeypatch.setattr(processing.ProcessingPipeline, "analyze_file", explode)
        history.undo(analysis)

    def test_packets_still_point_at_live_documents_after_undo(
        self, grouping, analysis, history
    ) -> None:
        """A snapshot must not leave packets referencing orphaned copies."""
        target = next(g for g in analysis.groups if g.page_count > 1)
        history.record(
            "Split",
            analysis,
            lambda: grouping.split_before(analysis, target.page_indexes[1]),
        )
        history.undo(analysis)

        live = {id(g) for g in analysis.groups}
        for packet in analysis.packets:
            for document in packet.documents:
                assert id(document) in live, (
                    "a packet points at a document that is no longer in the file"
                )


class TestUndoRestoresPageState:
    """Group ranges are only half the state. A correction also rewrites the
    pages themselves, and an undo that leaves those rewritten produces a model
    whose documents and pages disagree -- which surfaces later as a wrong
    export or a review flag nobody can explain."""

    def _page_state(self, analysis, page_index: int) -> dict:
        page = analysis.page(page_index)
        assert page is not None
        return {
            "predicted_type": page.predicted_type,
            "classification_confidence": page.classification_confidence,
            "classification_source": page.classification_source,
            "starts_new_document": page.starts_new_document,
            "boundary_confidence": page.boundary_confidence,
            "boundary_reasons": list(page.boundary_reasons),
            "reasoning_summary": page.reasoning_summary,
            "candidate_name": page.candidate.name,
            "separator_label": page.separator_label,
            "separator_state": page.separator_state,
            "excluded": page.excluded,
            "requires_review": page.requires_review,
            "review_reasons": list(page.review_reasons),
        }

    def _all_pages(self, analysis) -> dict[int, dict]:
        return {p.page_index: self._page_state(analysis, p.page_index) for p in analysis.pages}

    def test_retype_undo_restores_page_predicted_type(
        self, grouping, analysis, history
    ) -> None:
        group = analysis.groups[1]
        page_index = group.page_indexes[0]
        before = self._page_state(analysis, page_index)

        history.record(
            "Retype",
            analysis,
            lambda: grouping.set_document_type(analysis, group, COVER_LETTER),
        )
        assert analysis.page(page_index).predicted_type == COVER_LETTER

        history.undo(analysis)
        assert self._page_state(analysis, page_index) == before

    def test_retype_undo_restores_classification_source_and_review(
        self, grouping, analysis, history
    ) -> None:
        group = analysis.groups[1]
        page_index = group.page_indexes[0]
        before = self._page_state(analysis, page_index)

        history.record(
            "Retype",
            analysis,
            lambda: grouping.set_document_type(analysis, group, COVER_LETTER),
        )
        history.undo(analysis)
        after = self._page_state(analysis, page_index)

        assert after["classification_source"] == before["classification_source"]
        assert after["requires_review"] == before["requires_review"]
        assert after["review_reasons"] == before["review_reasons"]

    def test_split_undo_restores_starts_new_document(
        self, grouping, analysis, history
    ) -> None:
        target = next(g for g in analysis.groups if g.page_count > 1)
        seam = target.page_indexes[1]
        before = self._page_state(analysis, seam)
        assert before["starts_new_document"] is False

        history.record("Split", analysis, lambda: grouping.split_before(analysis, seam))
        assert analysis.page(seam).starts_new_document is True

        history.undo(analysis)
        assert self._page_state(analysis, seam) == before

    def test_merge_undo_restores_boundary_state(
        self, grouping, analysis, history
    ) -> None:
        second = analysis.groups[1]
        seam = second.page_indexes[0]
        before = self._page_state(analysis, seam)

        history.record(
            "Merge",
            analysis,
            lambda: grouping.merge_with_previous(analysis, second),
        )
        assert analysis.page(seam).starts_new_document is False

        history.undo(analysis)
        after = self._page_state(analysis, seam)
        assert after["starts_new_document"] == before["starts_new_document"]
        assert after["boundary_confidence"] == before["boundary_confidence"]
        assert after["boundary_reasons"] == before["boundary_reasons"]

    def test_page_move_undo_restores_classification_state(
        self, grouping, analysis, history
    ) -> None:
        first, second = analysis.groups[0], analysis.groups[1]
        moving = first.page_indexes[-1]
        before = self._page_state(analysis, moving)

        history.record(
            "Move page", analysis, lambda: grouping.move_pages(analysis, [moving], second)
        )
        assert analysis.page(moving).classification_source is not before["classification_source"]

        history.undo(analysis)
        assert self._page_state(analysis, moving) == before

    def test_exclude_undo_restores_page_exclusion(
        self, grouping, analysis, history
    ) -> None:
        group = analysis.groups[0]
        page_index = group.page_indexes[0]
        assert analysis.page(page_index).excluded is False

        history.record(
            "Exclude",
            analysis,
            lambda: grouping.set_group_excluded(analysis, group, True),
        )
        assert analysis.page(page_index).excluded is True

        history.undo(analysis)
        assert analysis.page(page_index).excluded is False

    def test_separator_toggle_undo_restores_separator_state(
        self, grouping, pipeline, samples_dir, history
    ) -> None:
        from app.models.enums import SeparatorPolicy
        from tests.helpers import build_pipeline

        separated = build_pipeline(
            grouping.profile, grouping.thresholds, separator_policy=SeparatorPolicy.EXCLUDE
        ).analyze_file(samples_dir / sample_data.sample_f().filename)

        page = next(p for p in separated.pages if p.is_separator)
        group = separated.group_for_page(page.page_index)
        before = page.separator_state

        history.record(
            "Keep separator",
            separated,
            lambda: grouping.set_separator_included(
                separated, group, page.page_index, True
            ),
        )
        assert separated.page(page.page_index).separator_state != before

        history.undo(separated)
        assert separated.page(page.page_index).separator_state == before

    def test_candidate_rename_undo_restores_identity(
        self, packets, analysis, history
    ) -> None:
        packet = analysis.identified_packets[0]
        original = packet.candidate.name

        history.record(
            "Rename",
            analysis,
            lambda: packets.rename_candidate(packet, "Someone Else Entirely"),
        )
        history.undo(analysis)

        assert analysis.identified_packets[0].candidate.name == original

    def test_repeated_cycles_produce_identical_page_state(
        self, grouping, analysis, history
    ) -> None:
        """The stack's own copies must survive being replayed."""
        before = self._all_pages(analysis)
        group = analysis.groups[1]

        history.record(
            "Retype",
            analysis,
            lambda: grouping.set_document_type(analysis, group, COVER_LETTER),
        )
        after = self._all_pages(analysis)
        assert after != before

        for _ in range(3):
            history.undo(analysis)
            assert self._all_pages(analysis) == before
            history.redo(analysis)
            assert self._all_pages(analysis) == after

    def test_a_page_only_change_is_still_recorded(
        self, grouping, pipeline, samples_dir, history
    ) -> None:
        """A correction that moves no pages must still be undoable."""
        from app.models.enums import SeparatorPolicy
        from tests.helpers import build_pipeline

        separated = build_pipeline(
            grouping.profile, grouping.thresholds, separator_policy=SeparatorPolicy.EXCLUDE
        ).analyze_file(samples_dir / sample_data.sample_f().filename)

        page = next(p for p in separated.pages if p.is_separator)
        group = separated.group_for_page(page.page_index)

        history.record(
            "Keep separator",
            separated,
            lambda: grouping.set_separator_included(
                separated, group, page.page_index, True
            ),
        )
        assert history.can_undo, "a page-level correction was not recorded"

    def test_snapshots_do_not_copy_page_text(self, analysis) -> None:
        """A snapshot per correction must not duplicate the whole document."""
        from app.services.correction_history import _capture

        snapshot = _capture(analysis)
        for state in snapshot.pages.values():
            assert "extracted_text" not in state
        assert any(p.extracted_text for p in analysis.pages), "fixture has no text at all"
