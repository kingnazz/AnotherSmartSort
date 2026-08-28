"""The type-first drag-and-drop board.

Drag gestures themselves are Qt's business; what matters here is that a drop
reaches the domain and changes the model correctly, that an unsafe drop is
refused rather than silently doing something else, and that everything a drag
does can be undone.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for the UI tests")

from PySide6.QtCore import QMimeData, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.profiles.recruiting import COVER_LETTER, RESUME  # noqa: E402
from app.services.confidence import ConfidenceThresholds  # noqa: E402
from app.ui.theme import palette_for  # noqa: E402
from app.ui.widgets.type_board import (  # noqa: E402
    DOCUMENT_MIME,
    NEEDS_REVIEW_LANE,
    PAGES_MIME,
    PageDrag,
    TypeBoard,
)
from scripts import sample_data  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def analysis(pipeline, samples_dir: Path):
    return pipeline.analyze_file(samples_dir / sample_data.sample_a().filename)


@pytest.fixture
def board(qapp, profile):
    widget = TypeBoard(
        list(profile.document_types), ConfidenceThresholds(), palette_for("light")
    )
    yield widget
    widget.deleteLater()


class TestPageDragPayload:
    """The drag payload has to survive the round trip through Qt's clipboard."""

    def test_a_payload_round_trips(self) -> None:
        payload = PageDrag(source_pdf="/tmp/a.pdf", page_indexes=(3, 4, 5))
        restored = PageDrag.decode(payload.encode())
        assert restored == payload

    def test_a_windows_path_round_trips(self) -> None:
        payload = PageDrag(source_pdf=r"C:\Users\x\My PDFs\a.pdf", page_indexes=(1,))
        assert PageDrag.decode(payload.encode()) == payload

    @pytest.mark.parametrize(
        "payload", [b"", b"no-separator", b"/tmp/a.pdf|", b"|1,2", b"/tmp/a.pdf|x,y"]
    )
    def test_malformed_payloads_are_rejected(self, payload: bytes) -> None:
        assert PageDrag.decode(payload) is None


class TestBoardLayout:
    def test_documents_are_laid_out_in_type_lanes(self, board, analysis) -> None:
        board.load([analysis])
        counts = board.lane_counts()

        assert counts["Application Report"] == 1
        assert counts[RESUME] == 1
        assert counts[COVER_LETTER] == 1
        assert counts["References"] == 1

    def test_every_document_gets_a_card(self, board, analysis) -> None:
        board.load([analysis])
        for group in analysis.groups:
            assert board.card(group.id) is not None

    def test_a_flagged_document_lands_in_needs_review(self, board, pipeline, samples_dir) -> None:
        flagged = pipeline.analyze_file(samples_dir / sample_data.sample_e().filename)
        assert any(g.needs_attention for g in flagged.groups)

        board.load([flagged])
        assert board.lane_counts()[NEEDS_REVIEW_LANE] >= 1

    def test_the_type_filter_narrows_the_board(self, board, analysis) -> None:
        board.load([analysis], wanted=(RESUME,))
        counts = board.lane_counts()
        assert counts[RESUME] == 1
        assert counts[COVER_LETTER] == 0


class TestDroppingDocuments:
    def _drop_document(self, board, group_id: str, lane_name: str):
        lane = board.lane(lane_name)
        assert lane is not None
        mime = QMimeData()
        mime.setData(DOCUMENT_MIME, group_id.encode("utf-8"))
        lane.document_dropped.emit(group_id, lane.document_type)

    def test_dropping_into_a_lane_requests_that_type(self, board, analysis) -> None:
        board.load([analysis])
        seen: list[tuple[str, str]] = []
        board.retype_requested.connect(lambda gid, t: seen.append((gid, t)))

        target = analysis.groups[0]
        self._drop_document(board, target.id, COVER_LETTER)

        assert seen == [(target.id, COVER_LETTER)]

    def test_dropping_into_needs_review_does_nothing(self, board, analysis) -> None:
        """"Needs Review" is a state, not a type -- there is nothing to set."""
        board.load([analysis])
        seen: list[tuple[str, str]] = []
        board.retype_requested.connect(lambda gid, t: seen.append((gid, t)))

        self._drop_document(board, analysis.groups[0].id, NEEDS_REVIEW_LANE)
        assert seen == []


class TestDroppingPages:
    def test_dropping_pages_on_a_card_requests_a_move(self, board, analysis) -> None:
        board.load([analysis])
        seen: list[tuple] = []
        board.pages_move_requested.connect(
            lambda gid, src, pages: seen.append((gid, src, tuple(pages)))
        )

        target = analysis.groups[1]
        moving = analysis.groups[0].page_indexes[-1]
        payload = PageDrag(source_pdf=str(analysis.path), page_indexes=(moving,))
        board.card(target.id).pages_dropped.emit(target.id, payload)

        assert seen == [(target.id, str(analysis.path), (moving,))]


class TestMultiPageSelection:
    def test_ctrl_click_adds_and_removes_pages(self, board, analysis) -> None:
        board.load([analysis])
        group = next(g for g in analysis.groups if g.page_count >= 2)
        card = board.card(group.id)
        first, second = group.page_indexes[0], group.page_indexes[1]

        card.page_clicked.emit(group.id, first, Qt.KeyboardModifier.NoModifier)
        card.page_clicked.emit(group.id, second, Qt.KeyboardModifier.ControlModifier)
        assert board.selected_pages(str(analysis.path)) == (first, second)

        card.page_clicked.emit(group.id, second, Qt.KeyboardModifier.ControlModifier)
        assert board.selected_pages(str(analysis.path)) == (first,)

    def test_shift_click_selects_a_run(self, board, analysis) -> None:
        board.load([analysis])
        group = next(g for g in analysis.groups if g.page_count >= 3)
        card = board.card(group.id)
        first, last = group.page_indexes[0], group.page_indexes[2]

        card.page_clicked.emit(group.id, first, Qt.KeyboardModifier.NoModifier)
        card.page_clicked.emit(group.id, last, Qt.KeyboardModifier.ShiftModifier)

        assert board.selected_pages(str(analysis.path)) == tuple(range(first, last + 1))

    def test_a_plain_click_starts_a_new_selection(self, board, analysis) -> None:
        board.load([analysis])
        group = next(g for g in analysis.groups if g.page_count >= 2)
        card = board.card(group.id)
        first, second = group.page_indexes[0], group.page_indexes[1]

        card.page_clicked.emit(group.id, first, Qt.KeyboardModifier.NoModifier)
        card.page_clicked.emit(group.id, second, Qt.KeyboardModifier.ControlModifier)
        card.page_clicked.emit(group.id, first, Qt.KeyboardModifier.NoModifier)

        assert board.selected_pages(str(analysis.path)) == (first,)


class TestThumbnailsAreLazy:
    def test_only_visible_cards_are_requested(self, board, analysis) -> None:
        """Nothing is visible until shown, so nothing should be requested."""
        board.load([analysis])
        assert board.visible_page_requests() == []

    def test_the_worker_queue_is_bounded(self) -> None:
        """A 300-page file must not queue 300 renders ahead of what is on screen."""
        from app.workers.thumbnail_worker import _QUEUE_LIMIT, ThumbnailWorker

        worker = ThumbnailWorker()
        try:
            for index in range(_QUEUE_LIMIT * 3):
                worker.request("/tmp/big.pdf", index)
            assert worker.pending <= _QUEUE_LIMIT
        finally:
            worker.stop()

    def test_scrolling_away_cancels_queued_work(self) -> None:
        from app.workers.thumbnail_worker import THUMBNAIL_DPI, ThumbnailWorker

        worker = ThumbnailWorker()
        try:
            for index in range(10):
                worker.request("/tmp/big.pdf", index)
            keep = {("/tmp/big.pdf", 3, THUMBNAIL_DPI)}
            worker.cancel_all_except(keep)
            assert worker.pending == 1
        finally:
            worker.stop()

    def test_a_single_request_can_be_withdrawn(self) -> None:
        from app.workers.thumbnail_worker import ThumbnailWorker

        worker = ThumbnailWorker()
        try:
            worker.request("/tmp/big.pdf", 1)
            worker.request("/tmp/big.pdf", 2)
            worker.cancel("/tmp/big.pdf", 1)
            assert worker.pending == 1
        finally:
            worker.stop()
