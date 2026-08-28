"""Reviewing a large file: lazy thumbnails, and where the time actually goes.

A 300-page bulk compile is the size the client's real work arrives at, and it
is where a review workspace either stays usable or stops being one. Two things
decide that, and both are measured here rather than asserted from intuition:

*Thumbnails must be lazy and must stay lazy.* Rendering every page before
showing anything is what made large files feel broken. The board asks only for
what is on screen, and -- the part that was missing -- asks again when the user
scrolls, otherwise everything below the fold stays a placeholder forever.

*The board must not be rebuilt for every correction.* Retyping one document
should touch that document, not reconstruct three hundred widgets.

The timing tests print what they measured and assert only against bounds
generous enough to survive a slow CI machine. A number that drifts an order of
magnitude is a regression worth failing over; one that drifts 30% on a busy
runner is not.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for the UI tests")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.enums import SeparatorPolicy  # noqa: E402
from app.profiles.recruiting import COVER_LETTER, RESUME  # noqa: E402
from app.services.confidence import ConfidenceThresholds  # noqa: E402
from app.services.parsers.registry import build_default_registry  # noqa: E402
from app.ui.theme import palette_for  # noqa: E402
from app.ui.widgets.type_board import (  # noqa: E402
    VIEWPORT_LOOKAHEAD,
    TypeBoard,
)
from scripts.pageup_fixtures import build_multi_attachment_compile  # noqa: E402
from tests.helpers import build_pipeline  # noqa: E402

pytestmark = pytest.mark.gui

#: Roughly a laptop screen. The board is measured at a real size because a
#: 100x30 default viewport would make every laziness result meaningless.
BOARD_SIZE = (1400, 900)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def big_file(tmp_path_factory: pytest.TempPathFactory, profile, thresholds) -> Path:
    """A 300-page compile: 30 applicants, each with three documents."""
    specs = [
        (f"Applicant {index:02d} Fernsby", 6, [(COVER_LETTER, 1), (RESUME, 3)])
        for index in range(30)
    ]
    batch = build_multi_attachment_compile(specs, filename="PageUp_Large_Review.pdf")
    assert batch.page_count == 301, batch.page_count
    return batch.write(tmp_path_factory.mktemp("large-review"))


@pytest.fixture(scope="module")
def big_analysis(big_file: Path, profile, thresholds):
    pipeline = build_pipeline(
        profile,
        thresholds,
        separator_policy=SeparatorPolicy.EXCLUDE,
        parser_registry=build_default_registry(profile),
    )
    started = time.perf_counter()
    analysis = pipeline.analyze_file(big_file)
    print(f"\n[measured] analyse 301 pages: {time.perf_counter() - started:.2f}s")
    return analysis


@pytest.fixture
def board(qapp, profile):
    widget = TypeBoard(
        list(profile.document_types), ConfidenceThresholds(), palette_for("light")
    )
    widget.resize(*BOARD_SIZE)
    yield widget
    widget.hide()
    widget.deleteLater()


def shown(board, qapp) -> None:
    """Lay the board out at a real size, as a visible window would."""
    board.show()
    qapp.processEvents()


class TestTheFixtureIsActuallyLarge:
    def test_three_hundred_pages_and_ninety_documents(self, big_analysis) -> None:
        assert big_analysis.page_count == 301
        documents = [g for g in big_analysis.groups if g.export_page_indexes]
        assert len(documents) == 90, len(documents)

    def test_every_applicant_is_separated(self, big_analysis) -> None:
        names = {
            g.candidate.name for g in big_analysis.groups if g.export_page_indexes
        }
        assert len(names) == 30, sorted(names)


class TestThumbnailsStayLazy:
    def test_a_hidden_board_asks_for_nothing(self, board, big_analysis) -> None:
        board.load([big_analysis])
        assert board.visible_page_requests() == []

    def test_a_shown_board_asks_for_a_screenful_not_a_fileful(
        self, board, big_analysis, qapp
    ) -> None:
        board.load([big_analysis])
        shown(board, qapp)

        requested = board.visible_page_requests()
        print(f"[measured] visible thumbnail requests: {len(requested)} of 301 pages")
        assert requested, "a visible board asked for no thumbnails at all"
        assert len(requested) < 301, "the whole file was requested at once"

    def test_scrolling_asks_for_what_scrolled_into_view(
        self, board, big_analysis, qapp
    ) -> None:
        """The bug this covers: the board asked once, on load, and never again."""
        board.load([big_analysis])
        shown(board, qapp)

        lane = board.lane(RESUME)
        assert lane is not None
        before = set(board.visible_page_requests())

        bar = lane._scroll.verticalScrollBar()
        assert bar.maximum() > 0, "the resume lane did not overflow its viewport"
        bar.setValue(bar.maximum())
        qapp.processEvents()

        after = set(board.visible_page_requests())
        print(f"[measured] requests before scroll {len(before)}, after {len(after)}")
        assert after, "scrolling to the bottom produced no thumbnail requests"
        assert after - before, "scrolling revealed no pages that were not already asked for"

    def test_scrolling_emits_a_fresh_request_batch(
        self, board, big_analysis, qapp
    ) -> None:
        board.load([big_analysis])
        shown(board, qapp)

        batches: list[list] = []
        board.thumbnails_needed.connect(lambda requests: batches.append(list(requests)))

        lane = board.lane(RESUME)
        bar = lane._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        # The board waits for the scroll to settle before asking, so a drag
        # queues one batch rather than one per pixel.
        board.refresh_visible_thumbnails(immediate=True)

        assert batches, "scrolling never asked the workspace for thumbnails"
        assert batches[-1], "the batch after scrolling was empty"

    def test_a_scroll_gesture_queues_one_batch_not_one_per_step(
        self, board, big_analysis, qapp
    ) -> None:
        board.load([big_analysis])
        shown(board, qapp)

        batches: list[int] = []
        board.thumbnails_needed.connect(lambda requests: batches.append(len(requests)))

        bar = board.lane(RESUME)._scroll.verticalScrollBar()
        for value in range(0, bar.maximum(), max(1, bar.maximum() // 40)):
            bar.setValue(value)
        assert not batches, "each scrollbar step asked for thumbnails immediately"

        board.refresh_visible_thumbnails(immediate=True)
        assert len(batches) == 1

    def test_pages_just_below_the_fold_are_rendered_ahead(
        self, board, big_analysis, qapp, monkeypatch
    ) -> None:
        """Scrolling should meet finished pictures, not placeholders."""
        board.load([big_analysis])
        shown(board, qapp)

        with_lookahead = len(board.visible_page_requests())

        monkeypatch.setattr("app.ui.widgets.type_board.VIEWPORT_LOOKAHEAD", 0.0)
        strictly_visible = len(board.visible_page_requests())

        print(
            f"[measured] on screen {strictly_visible} pages, "
            f"requested {with_lookahead} with {VIEWPORT_LOOKAHEAD:.0%} look-ahead"
        )
        assert with_lookahead > strictly_visible, (
            "nothing beyond the fold was requested, so scrolling would show "
            "placeholders while it caught up"
        )


class TestMeasuredCost:
    """Real numbers for the operations a large review repeats.

    Bounds are loose on purpose. The point is to catch an order-of-magnitude
    regression -- a full-file thumbnail sweep creeping back, a per-card rebuild
    becoming quadratic -- not to police a busy CI machine's variance.
    """

    def test_building_the_whole_board(self, board, big_analysis, qapp) -> None:
        started = time.perf_counter()
        board.load([big_analysis])
        shown(board, qapp)
        elapsed = time.perf_counter() - started
        print(f"[measured] build board, 90 cards / 301 pages: {elapsed * 1000:.0f}ms")
        assert elapsed < 15.0

    def test_rebuilding_after_a_correction(self, board, big_analysis, qapp) -> None:
        """Every correction currently rebuilds the board; measure what that costs."""
        board.load([big_analysis])
        shown(board, qapp)

        started = time.perf_counter()
        board.load([big_analysis])
        qapp.processEvents()
        elapsed = time.perf_counter() - started
        print(f"[measured] rebuild after one correction: {elapsed * 1000:.0f}ms")
        assert elapsed < 10.0

    def test_one_visibility_scan(self, board, big_analysis, qapp) -> None:
        """Runs on every scroll step, so it has to be far below a frame."""
        board.load([big_analysis])
        shown(board, qapp)

        started = time.perf_counter()
        for _ in range(20):
            board.visible_page_requests()
        elapsed = (time.perf_counter() - started) / 20
        print(f"[measured] one visibility scan, 90 cards: {elapsed * 1000:.1f}ms")
        assert elapsed < 0.10, "a scroll would stutter on the visibility scan alone"

    def test_filtering_the_board_to_one_type(self, board, big_analysis, qapp) -> None:
        board.load([big_analysis])
        shown(board, qapp)

        started = time.perf_counter()
        board.load([big_analysis], wanted=(RESUME,))
        qapp.processEvents()
        elapsed = time.perf_counter() - started
        print(f"[measured] filter to one type: {elapsed * 1000:.0f}ms")
        assert elapsed < 10.0
        assert board.lane_counts()[COVER_LETTER] == 0
