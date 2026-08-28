"""Refiling a document under a different applicant.

Two routes to the same domain call: the right-click menu and dragging the
document onto a candidate. Both must reach an *existing* candidate -- the bug
this covers was "Move to candidate…" opening the new-candidate prompt, which
left reassignment unreachable from the board.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for the UI tests")

from PySide6.QtCore import QMimeData  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from app.services.confidence import ConfidenceThresholds  # noqa: E402
from app.ui.theme import palette_for  # noqa: E402
from app.ui.widgets.type_board import (  # noqa: E402
    DOCUMENT_MIME,
    MOVE_TO_CANDIDATE,
    NEW_CANDIDATE,
    RENAME_CANDIDATE,
    build_context_menu,
)
from scripts import sample_data  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp, grouping, packets, profile, monkeypatch):
    """A review workspace loaded with a two-candidate file."""
    from app.ui.review_view import ReviewView

    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    )
    widget = ReviewView(
        grouping,
        ConfidenceThresholds(),
        palette_for("light"),
        list(profile.document_types),
        packets=packets,
    )
    yield widget
    widget.shutdown()
    widget.deleteLater()


@pytest.fixture
def two_candidates(pipeline, samples_dir: Path):
    """sample_g holds two different applicants in one PDF."""
    return pipeline.analyze_file(samples_dir / sample_data.sample_g().filename)


class TestContextMenu:
    def test_move_and_create_are_separate_actions(self, qapp) -> None:
        """Overloading one action was the bug; they are different decisions."""
        menu = build_context_menu(None, ["Resume", "Cover Letter"])
        labels = [action.text() for action in menu.actions()]

        assert MOVE_TO_CANDIDATE in labels
        assert NEW_CANDIDATE in labels
        assert RENAME_CANDIDATE in labels
        assert MOVE_TO_CANDIDATE != NEW_CANDIDATE


class TestMoveToExistingCandidate:
    def test_it_offers_the_other_candidates_and_moves(
        self, view, two_candidates, monkeypatch
    ) -> None:
        view.load([two_candidates])
        assert len(two_candidates.identified_packets) >= 2

        document = two_candidates.groups[0]
        original = two_candidates.packet_for_document(document)
        other = next(
            p for p in two_candidates.identified_packets if p.id != original.id
        )

        offered: list[list[str]] = []

        def choose(_parent, _title, _label, items, *_args, **_kwargs):
            offered.append(list(items))
            return other.display_name, True

        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(choose))
        view._move_to_existing_candidate(document.id)

        assert offered, "the user was never shown any candidates to choose from"
        assert other.display_name in offered[0]
        assert original.display_name not in offered[0], (
            "the document's current candidate was offered as a destination"
        )
        assert document.packet_id == other.id

    def test_the_move_is_manual_and_no_longer_flagged(
        self, view, two_candidates, monkeypatch
    ) -> None:
        view.load([two_candidates])
        document = two_candidates.groups[0]
        original = two_candidates.packet_for_document(document)
        other = next(p for p in two_candidates.identified_packets if p.id != original.id)

        monkeypatch.setattr(
            QInputDialog, "getItem", staticmethod(lambda *a, **k: (other.display_name, True))
        )
        view._move_to_existing_candidate(document.id)

        assert document.association_manually_set
        assert not document.association_review

    def test_cancelling_changes_nothing(self, view, two_candidates, monkeypatch) -> None:
        view.load([two_candidates])
        document = two_candidates.groups[0]
        before = document.packet_id

        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
        view._move_to_existing_candidate(document.id)

        assert document.packet_id == before

    def test_it_never_opens_the_new_candidate_prompt(
        self, view, two_candidates, monkeypatch
    ) -> None:
        """The exact regression: this used to ask for a new name instead."""
        view.load([two_candidates])
        asked_for_text: list[int] = []
        monkeypatch.setattr(
            QInputDialog,
            "getText",
            staticmethod(lambda *a, **k: asked_for_text.append(1) or ("", False)),
        )
        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))

        view._move_to_existing_candidate(two_candidates.groups[0].id)
        assert not asked_for_text, "Move to candidate opened the new-candidate prompt"

    def test_with_nobody_else_it_says_so_rather_than_failing_silently(
        self, view, pipeline, samples_dir: Path, monkeypatch
    ) -> None:
        single = pipeline.analyze_file(samples_dir / sample_data.sample_b().filename)
        view.load([single])

        shown: list[int] = []
        monkeypatch.setattr(
            QMessageBox,
            "information",
            staticmethod(lambda *a, **k: shown.append(1) or QMessageBox.StandardButton.Ok),
        )
        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))

        view._move_to_existing_candidate(single.groups[0].id)
        assert shown, "a file with one candidate gave no explanation at all"


class TestUndoRedoOfCandidateMoves:
    def test_undo_restores_the_original_candidate(
        self, view, two_candidates, monkeypatch
    ) -> None:
        view.load([two_candidates])
        document = two_candidates.groups[0]
        original = document.packet_id
        other = next(
            p for p in two_candidates.identified_packets if p.id != original
        )

        monkeypatch.setattr(
            QInputDialog, "getItem", staticmethod(lambda *a, **k: (other.display_name, True))
        )
        view._move_to_existing_candidate(document.id)
        assert view.history.can_undo

        view.undo()
        restored = next(g for g in two_candidates.groups if g.id == document.id)
        assert restored.packet_id == original

    def test_redo_reapplies_the_move(self, view, two_candidates, monkeypatch) -> None:
        view.load([two_candidates])
        document = two_candidates.groups[0]
        other = next(
            p for p in two_candidates.identified_packets if p.id != document.packet_id
        )
        target = other.id

        monkeypatch.setattr(
            QInputDialog, "getItem", staticmethod(lambda *a, **k: (other.display_name, True))
        )
        view._move_to_existing_candidate(document.id)
        view.undo()
        view.redo()

        moved = next(g for g in two_candidates.groups if g.id == document.id)
        assert moved.packet_id == target

    def test_creating_a_candidate_is_undoable_too(
        self, view, two_candidates, monkeypatch
    ) -> None:
        view.load([two_candidates])
        document = two_candidates.groups[0]
        before = len(two_candidates.packets)

        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: ("Someone New", True))
        )
        view._new_candidate_from_document(document.id)
        assert len(two_candidates.packets) != before or any(
            p.candidate.name == "Someone New" for p in two_candidates.packets
        )

        view.undo()
        assert not any(p.candidate.name == "Someone New" for p in two_candidates.packets)


class TestDragOntoCandidate:
    def test_a_candidate_block_accepts_a_dragged_document(
        self, qapp, two_candidates
    ) -> None:
        from app.ui.widgets.packet_section import PacketSection

        packet = two_candidates.identified_packets[0]
        section = PacketSection(packet, palette_for("light"), ConfidenceThresholds())
        try:
            assert section.acceptDrops()

            received: list[tuple[str, str]] = []
            section.document_dropped.connect(
                lambda packet_id, group_id: received.append((packet_id, group_id))
            )
            section.document_dropped.emit(packet.id, "grp-test")
            assert received == [(packet.id, "grp-test")]
        finally:
            section.deleteLater()

    def test_a_dragged_document_carries_the_shared_payload(self, qapp) -> None:
        """The board and the candidate view must speak the same drag format."""
        mime = QMimeData()
        mime.setData(DOCUMENT_MIME, b"grp-000123")
        assert mime.hasFormat(DOCUMENT_MIME)
        assert bytes(mime.data(DOCUMENT_MIME)).decode() == "grp-000123"

    def test_dropping_onto_a_candidate_moves_the_document(
        self, view, two_candidates
    ) -> None:
        view.load([two_candidates])
        view.set_view_mode(1)  # candidate view

        document = two_candidates.groups[0]
        other = next(
            p for p in two_candidates.identified_packets if p.id != document.packet_id
        )
        view.move_document_to_candidate(document.id, other.id)

        assert document.packet_id == other.id
        assert document.association_manually_set
